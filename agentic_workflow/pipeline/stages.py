"""
pipeline/stages.py
==================
All pipeline stage functions and the main run_pipeline() orchestrator.

Each stage_*() function:
  - Takes state: PipelineState as its only argument
  - Calls run_agent() from utils
  - Mutates state with its outputs
  - Returns bool (success/fail) or tuple[bool, str] (result, feedback)

run_pipeline() wires the stages together, handles the retry loops,
and writes the final report.

No SCG/DCG file paths are used here. All call graph context comes from
state.boundary — the structured boundary entry parsed from boundaries.json.
"""

import json
import logging
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.agents import build_agent_options
from pipeline.analysis import run_analysis_interface, run_annotated_sa
from pipeline.utils import (
    run_agent,
    parse_json_from_response,
    write_final_report,
    print_success_summary,
    print_failure_summary,
)
from config import (
    MAX_LOOPS,
    OUT_REPORT,
    OUT_TEST_CASE_DIR,
    OUT_ANNOTATED_TOOL_DIR,
    OUT_EXECUTION_TRACE,
)

log = logging.getLogger("pipeline")


# ── Stage 3: Unsoundness reasoning report ─────────────────────────────────────

async def stage_generate_report(state: PipelineState) -> bool:
    """
    Delegates to the fn-edge-analyzer agent (via the Task tool) to produce a
    structured JSON unsoundness report for this boundary entry.

    On retry iterations, accumulated feedback from failed reproduction and
    trace checks is injected into the task prompt.
    """
    options     = build_agent_options(state)
    report_path = state.out(OUT_REPORT)
    feedback    = _build_feedback_section(state)

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

### Boundary entry

{state.boundary_summary()}

### Full boundary JSON

{state.boundary_json()}
{feedback}
### Output

Write a JSON file to {report_path} with this exact schema:
{{
  "edgeId":        "<short identifier, e.g. E1>",
  "caller":        "<fully-qualified caller method signature>",
  "callerLine":    <line number as integer>,
  "target":        "<fully-qualified missed callee method signature>",
  "pattern_class": "<e.g. reflection_indirect_instantiation | virtual_dispatch | native_call | ...>",
  "evidence": [
    "<path/to/file:line — observation>",
    ...
  ],
  "toolLimitation": {{
    "reason":     "<plain-English explanation of why the tool missed this edge>",
    "rootCause":  "<design_decision | precision_tradeoff | missing_model | unsound_approximation>",
    "toolCodeEvidence": {{
      "relevantFiles": ["<tool-source-file:line>", ...],
      "codeSnippet":   "<relevant code excerpt with inline comments>"
    }}
  }},
  "potentialTestCase": {{
    "description": "<what a minimal self-contained test case must do to trigger this edge>",
    "TestCaseMissingEdge": {{
      "caller": "<com.example.Caller.method()>",
      "callee": "<com.example.Target.method()>"
    }}
  }}
}}

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
        log.info(f"{state.label()} Report → {state.unsoundness_report}")
        log.info(f"{state.label()} Hypothesis: {result.get('root_cause_hypothesis', 'N/A')}")
        return True

    log.error(f"{state.label()} Report generation failed.")
    return False


# ── Stage 4: Test case generation ─────────────────────────────────────────────

async def stage_generate_test_case(state: PipelineState) -> bool:
    """
    Delegates to the test-case-generator agent to create two minimal Maven projects:
    one reproducing the unsoundness, one without the root cause.
    """
    options = build_agent_options(state)
    out_dir = state.out(OUT_TEST_CASE_DIR)

    state.reproducing_test_case_path = out_dir + "/reproducing"
    state.non_reproducing_test_case_path = out_dir + "/non_reproducing"

    response = await run_agent(
        prompt=f"""\
Use the test-case-generator agent to generate test cases for this boundary.

## Unsoundness report

{state.unsoundness_report}

## Output directories

Reproducing test case     : {state.reproducing_test_case_path}/
Non-reproducing test case: {state.non_reproducing_test_case_path}/

Return the JSON result from the agent directly.
## Return

Return JSON only:
{{
  "reproducing": "true | false (if generation failed)",
  "non_reproducing": "true | false (if generation failed or skipped)",
}}


""",
        options=options,
        stage_name=f"{state.label()} TestCaseGeneration",
    )

    result = parse_json_from_response(response)
    log.info(f"{state.label()} Test case generation result: {result}")
    if result:
        # repro = bool(result.get("reproducing", "false").lower() == "true")
        repro = bool(result.get("reproducing", False))
        # non_repro = bool(result.get("non_reproducing", "false").lower() == "true")
        non_repro = bool(result.get("non_reproducing", False))
        if not non_repro:
            log.warning(f"{state.label()} Non-reproducing test case generation failed or skipped.")
            state.non_reproducing_test_case_path = None
        if repro:
            log.info(f"{state.label()} Reproducing test case generated.")
            return True

    log.error(f"{state.label()} Test case generation failed.")
    return False


# ── Stage 5: Reproduction check ───────────────────────────────────────────────

async def stage_check_reproduction(state: PipelineState) -> tuple[bool, str]:
    """
    Run the CG analysis interface on the reproducing test case, then check
    whether the expected caller→callee edge from the unsoundness report
    appears in the boundary output (edges present in dyncg, absent in staticcg).

    No agent is needed — this is a direct lookup in the boundary JSON files.

    Returns:
        (reproduced: bool, feedback: str)
    """
    state.staticcg_path, state.dyncg_path, state.boundary_path = \
        run_analysis_interface(state)

    # Read expected edge from the test case directory
    try:
        base_path   = Path(state.reproducing_test_case_path)
        report      = json.loads((base_path / "expected_missed_edge.json").read_text())
        exp_caller  = report["caller"]
        exp_callee  = report["callee"]
    except Exception as e:
        msg = f"Could not read expected edge from test case report: {e}"
        log.error(f"{state.label()} {msg}")
        return False, msg

    log.info(f"{state.label()} Expected missing edge: {exp_caller} → {exp_callee}")

    # Search boundary files for the expected edge
    reproduced, feedback = _edge_in_boundary(state.boundary_path, exp_caller, exp_callee)
    log.info(f"{state.label()} Reproduced: {reproduced}")
    return reproduced, feedback


# ── Stage 6: Root cause annotation + trace check ──────────────────────────────

async def stage_annotate_and_check_trace(state: PipelineState) -> tuple[bool, str]:
    """
    6a. Root-cause-annotator agent adds targeted logging to the SA tool.
    6b. Run the annotated tool on the test case → modified analysis output.
    6c. Check whether the execution trace contains the expected log entries.

    Returns:
        (trace_contains_logs: bool, feedback: str)
    """
    options        = build_agent_options(state)
    annotated_out  = state.out(OUT_ANNOTATED_TOOL_DIR)
    # trace_log_path = state.out(OUT_EXECUTION_TRACE)
    trace_log_path = str(Path(state.reproducing_test_case_path) / OUT_STATICCG_DIR / OUT_EXECUTION_TRACE)

    # 6a: Annotate the SA tool
    annot_response = await run_agent(
        prompt=f"""\
Use the root-cause-annotator agent to annotate the SA tool for this boundary.

## Unsoundness report

{state.unsoundness_report}

## SA tool source

{state.sa_tool_path}

## Output directory

{annotated_out}/

Return the JSON result from the agent directly.

Return JSON only:

{{
  "annotated_tool_path": "<output_dir>/",
  "annotated_file":      "<relative/path/to/ModifiedFile.java>",
  "injected_at_line":    <line_number>,
  "skipped":             false,
  "skip_reason":         null
}}

If the log already exists for the same reason (`skipped: true`):

{{
  "annotated_tool_path": "<source_dir>/",
  "annotated_file":      null,
  "injected_at_line":    null,
  "skipped":             true,
  "skip_reason":         "Existing [UNSOUNDNESS] log with same reason found at <file>:<line>"
}}""",
        options=options,
        stage_name=f"{state.label()} RootCauseAnnotation",
    )

    annot_result = parse_json_from_response(annot_response)
    log.info(f"{state.label()} Annotation result: {annot_result}")
    if not annot_result or "annotated_tool_path" not in annot_result:
        log.error(f"{state.label()} Annotation failed.")
        return False, "Annotation step failed — could not parse result."

    state.annotated_sa_tool_path = annot_result["annotated_tool_path"]

    if annot_result.get("skipped"):
        log.info(f"{state.label()} Annotation skipped: {annot_result.get('skip_reason')}")
    else:
        log.info(
            f"{state.label()} Annotated {annot_result.get('annotated_file')} "
            f"at line {annot_result.get('injected_at_line')}"
        )

    # Log format is standardised — always grep for the [UNSOUNDNESS] prefix
    expected_pattern = "[UNSOUNDNESS]"

    # 6b: Run annotated tool → modified analysis output
    state.modified_analysis_path = run_annotated_sa(state)

    # 6c: Check execution trace
    trace_response = await run_agent(
        prompt=f"""\
Check whether the execution trace from the annotated SA tool confirms
the root cause of the unsoundness.

## Expected evidence

The trace should contain entries matching: {expected_pattern}

## Files to examine

Execution trace : {trace_log_path}
Unsoundness report: {state.unsoundness_report}
Test case       : {state.reproducing_test_case_path}

## Your task

1. Read the execution trace at: {trace_log_path}
2. Search for entries matching: {expected_pattern}
3. Cross-check with the unsoundness report and test case to see if the log entries align with the expected root cause and callsite. \
Remember that the log entries may happen for different edges or callsites that are not the one we expect (like internal libraries we are not interested in).
4. Return your finding.

Return JSON only:
{{
  "trace_contains_logs": true | false,
  "feedback": "<what is present or missing; quote relevant trace lines if found>"
}}""",
        options=options,
        stage_name=f"{state.label()} TraceCheck",
    )

    trace_result = parse_json_from_response(trace_response)
    if trace_result:
        has_logs = bool(trace_result.get("trace_contains_logs", False))
        # has_logs = bool(trace_result.get("trace_contains_logs", "false").lower() == "true")
        feedback = trace_result.get("feedback", "No feedback provided.")
        log.info(f"{state.label()} Trace contains logs: {has_logs}")
        return has_logs, feedback

    log.warning(f"{state.label()} Could not parse trace check — assuming logs not found.")
    return False, "Could not parse trace check result."


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(state: PipelineState) -> None:
    log.info(f"{state.label()} {'=' * 50}")
    log.info(f"{state.label()} PIPELINE START  {state.boundary_id}")
    log.info(f"{state.label()} {'=' * 50}")

    # Stages 3–6: main retry loop (no boundary selection stage —
    # each PipelineState already holds exactly one boundary entry)
    while state.loop_count <= MAX_LOOPS:
        label = f"Loop {state.loop_count}" if state.loop_count > 0 else "Initial run"
        log.info(f"{state.label()} {'─' * 40} {label}")

        # Stage 3: Generate unsoundness report
        if not await stage_generate_report(state):
            write_final_report(state, success=False, reason="Report generation failed.")
            return

        # Stage 4: Generate test case
        if not await stage_generate_test_case(state):
            write_final_report(state, success=False, reason="Test case generation failed.")
            return

        # Stage 5: Check reproduction (check 1)
        reproduced, repr_feedback = await stage_check_reproduction(state)
        if not reproduced:
            state.reproduce_feedback.append(repr_feedback)
            log.info(f"{state.label()} Not reproduced. Feedback recorded.")
            if state.loop_count >= MAX_LOOPS:
                break
            state.loop_count += 1
            continue   # → back to stage 3 with reproduce_feedback injected

        log.info(f"{state.label()} Reproduced. Proceeding to annotation...")

        # Stage 6: Annotate + check trace (check 2)
        trace_ok, trace_feedback = await stage_annotate_and_check_trace(state)
        if trace_ok:
            write_final_report(
                state,
                success=True,
                reason="Root cause identified and confirmed via execution trace.",
            )
            print_success_summary(state)
            return

        state.trace_feedback.append(trace_feedback)
        log.info(f"{state.label()} Trace logs not found. Feedback recorded.")
        if state.loop_count >= MAX_LOOPS:
            break
        state.loop_count += 1
        # → back to stage 3 with trace_feedback injected

    # Max loops reached
    write_final_report(
        state,
        success=False,
        reason=(
            f"Exceeded maximum loop count ({MAX_LOOPS}). "
            f"Reproduce feedbacks: {state.reproduce_feedback}. "
            f"Trace feedbacks: {state.trace_feedback}."
        ),
    )
    print_failure_summary(state)


# ── Private helpers ───────────────────────────────────────────────────────────

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


def _descriptor_to_classname(descriptor: str) -> str:
    """Convert a JVM class descriptor to a dotted class name.
    e.g. 'Lcom/example/App;' → 'com.example.App'
    Already-dotted names (no slashes) are returned as-is.
    """
    return descriptor.lstrip("L").rstrip(";").replace("/", ".")


def _entry_matches(entry: dict, expected_caller: str, expected_callee: str) -> bool:
    """
    Check whether a structured boundary entry matches the expected caller and
    callee strings from TestCaseMissingEdge.

    Expected format:  'com.example.App.runParser()'
    Boundary format:  caller/missedCallee dicts with declaringClass + name fields.

    Matching is done by checking that both the normalized class name and the
    method name of the boundary entry appear in the expected string, so minor
    differences in parameter format do not cause false negatives.
    """
    caller  = entry.get("caller", {})
    callee  = entry.get("missedCallee", {})

    caller_class  = _descriptor_to_classname(caller.get("declaringClass", ""))
    caller_method = caller.get("name", "")
    callee_class  = _descriptor_to_classname(callee.get("declaringClass", ""))
    callee_method = callee.get("name", "")

    caller_match = caller_class in expected_caller and caller_method in expected_caller
    callee_match = callee_class in expected_callee and callee_method in expected_callee
    return caller_match and callee_match


def _edge_in_boundary(
    boundary_path: str,
    expected_caller: str,
    expected_callee: str,
) -> tuple[bool, str]:
    """
    Parse the JSON file at boundary_path and check whether any entry structurally matches
    the expected caller → callee edge.

    Returns:
        (found: bool, feedback: str)
    """
    try:
        data = json.loads(Path(boundary_path).read_text())
        entries = data.get("boundaries", [])
    except (json.JSONDecodeError, AttributeError):
        log.warning(f"Could not parse boundary file: {boundary_path}")
        return False, f"Could not parse boundary file: {boundary_path}"

    for entry in entries:
        if _entry_matches(entry, expected_caller, expected_callee):
            caller = entry.get("caller", {})
            callee = entry.get("missedCallee", {})
            return True, (
                f"Edge confirmed in {Path(boundary_path).name}: "
                f"{_descriptor_to_classname(caller.get('declaringClass','?'))}"
                f".{caller.get('name','?')} → "
                f"{_descriptor_to_classname(callee.get('declaringClass','?'))}"
                f".{callee.get('name','?')}"
            )

    return False, (
        f"Edge not found in boundary output. "
        f"Expected: {expected_caller} → {expected_callee}. "
        f"Searched {Path(boundary_path).name}."
    )