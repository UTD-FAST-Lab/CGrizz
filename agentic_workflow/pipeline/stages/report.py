"""
pipeline/stages/report.py
==========================
Stage 3 (generate the unsoundness report) and stage 9 (refine it after the
trace check), plus the feedback / retry context injected on outer-loop retries.
"""

import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.agents import build_agent_options
from pipeline.agent_runner import run_agent, parse_json_from_response
from pipeline.report_schema import validate_file, root_cause_markdown
from config import OUT_REPORT, REPORT_SCHEMA_RETRIES

log = logging.getLogger("pipeline")


# ── Stage 3: Unsoundness reasoning report ─────────────────────────────────────


@timed("report")
async def stage_generate_report(state: PipelineState) -> bool:
    """
    Delegates to the fn-edge-analyzer agent (via the Task tool) to produce a
    structured JSON unsoundness report for this boundary entry.

    On retry iterations, accumulated feedback from failed reproduction and
    trace checks is injected into the task prompt.
    """
    options       = build_agent_options(state)
    report_path   = state.out(OUT_REPORT)
    _archive_previous_report(state)
    feedback      = _build_feedback_section(state)
    retry_context = _build_retry_context(state)

    response = await run_agent(
        prompt=f"""\
Use the fn-edge-analyzer agent to analyze the boundary entry below and write a
structured unsoundness report. Invoke it via the Task tool.

## Task for fn-edge-analyzer

A static analysis tool ({state.tool_name} / {state.algorithm}) missed a callee that
was observed dynamically. Analyze why and write a JSON report to: {report_path}

### Context

Program under analysis : {state.target_program_path}
Static analysis tool   : {state.sa_tool_path}
Tool / Algorithm       : {state.tool_name} / {state.algorithm}
Main class             : {state.main_class}

{root_cause_markdown()}

### Boundary entry

{state.boundary_summary()}


{feedback}
{retry_context}

## After fn-edge-analyzer completes

Return JSON only:
{{
  "report_path": "{report_path}",
  "root_cause_hypothesis": "<1–2 sentence summary of the root cause>",
  "sa_tool_area": "<suspected class or component in the SA tool>"
}}""",
        options=options,
        stage_name=f"{state.label()} GenerateReport",
    )

    result = parse_json_from_response(response)
    log.info(f"{state.label()} Report generation result: {result}")
    if result and "report_path" in result:
        state.unsoundness_report = result["report_path"]
        if Path(report_path).exists():
            state.unsoundness_report = report_path
        await _enforce_schema(state)
        return True

    if Path(report_path).exists():
        state.unsoundness_report = report_path
        log.warning(f"{state.label()} Could not parse agent response, but found report at {report_path}. Proceeding with it.")
        await _enforce_schema(state)
        return True

    log.error(f"{state.label()} Report generation failed.")
    return False


async def _enforce_schema(state: PipelineState) -> None:
    """
    Validate the report against pipeline/report_schema.py and let the analyzer fix
    it (REPORT_SCHEMA_RETRIES times). Remaining problems are recorded, not fatal.
    """
    tool_dir = Path(state.sa_tool_path)
    problems = validate_file(state.unsoundness_report, tool_dir)
    for _ in range(REPORT_SCHEMA_RETRIES):
        if not problems:
            break
        log.warning(f"{state.label()} Report does not match the schema: {problems}")
        await run_agent(
            prompt=f"""Use the fn-edge-analyzer agent to fix the format of the unsoundness report at
{state.unsoundness_report}. Invoke it via the Task tool. Keep the analysis; only fix:

{chr(10).join(f"- {p}" for p in problems)}

{root_cause_markdown()}

Overwrite the file in place. Return JSON only: {{"report_path": "{state.unsoundness_report}"}}""",
            options=build_agent_options(state),
            stage_name=f"{state.label()} FixReportSchema",
        )
        problems = validate_file(state.unsoundness_report, tool_dir)
    state.report_schema_problems = problems
    if problems:
        log.warning(f"{state.label()} Continuing with schema problems in the report: {problems}")


# ── Stage 9: Refine unsoundness report ───────────────────────────────────────


@timed("refine_report")
async def stage_refine_report(state: PipelineState) -> bool:
    """
    After trace confirmation, update the unsoundness report to replace the initial
    hypothesis with the confirmed code location found in the execution trace.
    Non-critical: failures are logged but do not halt the pipeline.
    """

    if not (state.unsoundness_report and Path(state.unsoundness_report).exists()):
        log.info(f"{state.label()} No report file — skipping report refinement.")
        return True

    options = build_agent_options(state)
    line_hint = f" at line {state.injected_at_line}" if state.injected_at_line else ""
    annot_hint = (
        f"\nAnnotated file : {state.annotated_sa_tool_path}/{state.annotated_file}{line_hint}"
        if state.annotated_file else ""
    )

    await run_agent(
        prompt=f"""\
The annotation and trace check have confirmed the root cause. Update the unsoundness
report to reflect the confirmed findings.

## Current unsoundness report
Path: {state.unsoundness_report}
(Read this file first.)

## Confirmed tool limitation location
{annot_hint}

## Task
1. Read the current report JSON.
2. Update these fields only (preserve ALL other fields verbatim):
   - `toolCodeEvidence.relevantFiles`: confirm or correct file and line number
   - `toolLimitation`: mention the confirmed code site
4. Overwrite the report at the same path: {state.unsoundness_report}

DO NOT change the format or add any field. 

Return JSON only:
{{
  "report_path": "{state.unsoundness_report}",
  "refined": true
}}""",
        options=options,
        stage_name=f"{state.label()} RefineReport",
    )

    log.info(f"{state.label()} Report refinement complete.")
    return True


def _build_feedback_section(state: PipelineState) -> str:
    """Build the feedback block injected into the report prompt on retries."""
    if not state.has_reproduce_feedback() and not state.has_trace_feedback():
        return ""

    lines = ["\n## Feedback from previous attempts\n"]
    if state.has_reproduce_feedback():
        lines.append("### Test case did not reproduce the unsoundness:")
        lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.reproduce_feedback)]
    if state.has_trace_feedback():
        lines.append("\n### Execution trace did not contain expected logs:")
        lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.trace_feedback)]
    lines.append("\nRevise your root cause hypothesis and test case strategy accordingly.")

    return "\n".join(lines)


def _archive_previous_report(state: PipelineState) -> None:
    """
    On outer-loop retries, rename the previous report so the new one does not
    overwrite it: unsoundness_report.json → unsoundness_report.loop<N>.<reason>.json,
    where N is the loop that produced it and reason is why it was sent back.
    """
    if state.loop_count == 0:
        return

    canonical = Path(state.out(OUT_REPORT))
    prev = Path(state.unsoundness_report) if state.unsoundness_report else canonical
    if not prev.exists():
        prev = canonical
    if not prev.exists():
        return

    produced_in = state.loop_count - 1
    reason = state.report_refine_reason or "refine"
    archived = canonical.with_name(f"{canonical.stem}.loop{produced_in}.{reason}{canonical.suffix}")
    shutil.move(str(prev), str(archived))

    state.report_history.append({"loop": produced_in, "reason": reason, "path": str(archived)})
    state.unsoundness_report = None
    state.report_refine_reason = None
    log.info(f"{state.label()} Archived previous report → {archived}")


def _build_retry_context(state: PipelineState) -> str:
    """
    Build the context block injected into the report prompt on retries.
    Surfaces the previous report, the reproducing test case, and the annotated
    SA tool file so the agent can read and revise its earlier analysis.
    Returns an empty string on the first (non-retry) run.
    """
    if state.loop_count == 0:
        return ""

    report_path = state.out(OUT_REPORT)
    lines = [
        "\n## Context from previous attempt\n",
        "IMPORTANT — FORMAT PRESERVATION: This is a retry. You MUST read the previous",
        "report file listed below, apply the feedback, and write a NEW revised report",
        f"to {report_path} using the EXACT SAME JSON schema as the previous one.",
        "Do not add, remove, or rename any top-level keys. Do not change the format.",
        "Only update field VALUES based on the feedback above.",
        "Do NOT modify or delete the previous report file — it is kept for history.\n",
    ]

    prev = state.report_history[-1] if state.report_history else None
    if prev and Path(prev["path"]).exists():
        lines += [
            "### Previous unsoundness report",
            "",
            f"Path: {prev['path']}",
            f"(Produced in loop {prev['loop']}; rejected due to: {prev['reason']}. "
            "Read this file to review your previous analysis before revising it based on the feedback above.)",
        ]

    if state.reproducing_test_case_path and Path(state.reproducing_test_case_path).exists():
        lines += [
            "",
            "### Reproducing test case",
            "",
            f"Path: {state.reproducing_test_case_path}",
            "(Read the Java source files here to understand how the test case was structured.)",
        ]

    # if not state.report_oracle_failure:
    #     if state.oracle_path and Path(state.oracle_path).exists() and state.instrumentation and Path(state.instrumentation).exists():
    #         lines += [
    #             "",
    #             "### Oracle for reproducing test case",
    #             "",
    #             f"Path: {state.oracle_path}",
    #             f"Path: {state.instrumentation}",
    #             "(Read this file to review the expected root causes and symptoms that the agent identified for the test case using the ASM instrumentation)",
    #         ]

    #     if state.target_oracle_path and Path(state.target_oracle_path).exists():
    #         lines += [
    #             "",
    #             "### Oracle produced by running instrumentation against the real target program",
    #             "",
    #             f"Path: {state.target_oracle_path}",
    #             "(This oracle was produced by running the instrumentation JAR against the actual target program. "
    #             "The boundary edge was NOT found in its symptoms — use it to understand what the instrumentation "
    #             "does observe at runtime and refine the root cause pattern accordingly.)",
    #         ]


    if state.annotated_sa_tool_path and state.annotated_file:
        line_hint = f" (line {state.injected_at_line})" if state.injected_at_line else ""
        lines += [
            "",
            "### Previously annotated SA tool file",
            "",
            f"Annotated Tool Path: {state.annotated_sa_tool_path}, File: {state.annotated_file}{line_hint}",
            "(Read this file to see the [UNSOUNDNESS] log that was injected. "
            "Use it to validate and refine your root cause hypothesis.)",
        ]

    return "\n".join(lines)
