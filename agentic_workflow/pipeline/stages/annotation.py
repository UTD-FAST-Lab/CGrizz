"""
pipeline/stages/annotation.py
==============================
Stage 7 (annotate the SA tool with one [UNSOUNDNESS] log and run it) and
stage 8 (check the execution trace).
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.agents import build_agent_options
from pipeline.analysis import run_annotated_sa
from pipeline.agent_runner import run_agent, parse_json_from_response
from pipeline.edges import is_reachable, rc_in_trace
from config import OUT_ANNOTATED_TOOL_DIR, OUT_EXECUTION_TRACE, OUT_STATICCG_DIR

log = logging.getLogger("pipeline")


# ── Stage 7: Root cause annotation ────────────────────────────────────────────


async def stage_root_cause_annotation(state: PipelineState) -> tuple[bool, str]:
    """
    Root-cause-annotator agent adds targeted logging to the SA tool,
    then runs the annotated tool on the test case to produce the execution trace.

    Returns:
        (success: bool, feedback: str)  — feedback is non-empty only on failure
    """
    options       = build_agent_options(state)
    annotated_out = state.out(OUT_ANNOTATED_TOOL_DIR)

    annotated_out_path = Path(annotated_out)

    # if annotated_out_path.exists():
    #     shutil.rmtree(annotated_out_path)
    #     log.info(f"{state.label()} Deleted stale instrumentation dir: {annotated_out_path}")

    if not annotated_out_path.exists():
        log.info(f"{state.label()} Copying SA tool to {annotated_out} ...")
        await asyncio.to_thread(shutil.copytree, state.sa_tool_path, annotated_out)
        log.info(f"{state.label()} SA tool copy done.")        

    annot_feedback = _build_annotation_feedback(state)

    annot_response = await run_agent(
        prompt=f"""\
Use the root-cause-annotator agent to annotate the SA tool for this boundary.
Invoke it via the Task tool.

## Unsoundness report

{state.unsoundness_report}

## SA tool source

{annotated_out}/
{annot_feedback}

Return the JSON result from the agent directly.

Return JSON only:

{{
  "annotated_tool_path": "<source_dir>/",
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
    state.annotated_file         = annot_result.get("annotated_file")
    state.injected_at_line       = annot_result.get("injected_at_line")

    if annot_result.get("skipped"):
        log.info(f"{state.label()} Annotation skipped: {annot_result.get('skip_reason')}")
    else:
        log.info(
            f"{state.label()} Annotated {state.annotated_file} "
            f"at line {state.injected_at_line}"
        )

    # Run annotated tool → produces the execution trace consumed by stage_check_trace
    state.modified_analysis_path = run_annotated_sa(state)
    return True, ""

# ── Stage 8: Trace check ───────────────────────────────────────────────────────


async def stage_check_trace(state: PipelineState) -> tuple[bool, str]:
    """
    Checks whether the execution trace produced by the annotated SA tool
    contains the expected [UNSOUNDNESS] log entries confirming the root cause.

    Returns:
        (trace_contains_logs: bool, feedback: str)
    """
    options        = build_agent_options(state)
    trace_log_path = str(Path(state.reproducing_test_case_path) / OUT_STATICCG_DIR / OUT_EXECUTION_TRACE)
    oracle_path    = state.oracle_path
    expected_pattern = "[UNSOUNDNESS]"

    # read the execution trace log
    try:
        trace_log = Path(trace_log_path).read_text()
        state.execution_trace_path = trace_log_path  # store for annotation feedback on retry
        unsoundness_lines = [line for line in trace_log.splitlines() if expected_pattern in line]
        log.info(f"{state.label()} Found {len(unsoundness_lines)} lines with pattern [UNSOUNDNESS] in execution trace.")
    except Exception as e:
        msg = f"Could not read execution trace log: {e}"
        log.error(f"{state.label()} {msg}")
        return False, msg

    # check for existance of pattern in the trace log
    if len(unsoundness_lines) == 0:
        log.error(f"{state.label()} Trace check failed — no lines with pattern [UNSOUNDNESS] found in execution trace.")
        return False, "Trace check failed — no lines with pattern [UNSOUNDNESS] found in execution trace."

    # read the root causes from the oracle
    # Oracle format: {"RC:method@pc": {"method": ..., "pc": ..., "symptoms": [...]}, ...}
    rc_methods = set()
    try:
        oracle = json.loads(Path(oracle_path).read_text())
        for rc in oracle.values():
            # if the root cause has symptoms
            if len(rc.get("symptoms")) > 0:
                rc_methods.add(rc.get("method"))
    except Exception as e:
        msg = f"Could not read oracle for root cause methods: {e}"
        log.error(f"{state.label()} {msg}")

    # check that all reachable root cause methods apear in the unsoundness lines
    for method in rc_methods:
        if not is_reachable(method, state.staticcg_path):
            log.warning(f"The method is not reachable for root cause: {method}")
            continue
        if not rc_in_trace(method, unsoundness_lines):
            log.error(f"{state.label()} Trace check failed — expected root cause method '{method}' not found in execution trace.")
            return False, f"Trace check failed — expected root cause method '{method}' not found in execution trace."

    log.info(f"{state.label()} Trace check passed — expected logs found in execution trace.")
    return True, "Trace check passed — expected logs found in execution trace."


def _build_annotation_feedback(state: PipelineState) -> str:
    """
    Build the feedback block injected into the annotation stage prompt on retries.
    Includes: prior trace failure messages, actual [UNSOUNDNESS] lines from the last
    trace, oracle root cause methods, and the exact log format the checker expects.
    """
    if not state.has_trace_feedback():
        return ""

    lines = ["\n## Feedback from previous annotation attempts\n"]
    lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.trace_feedback)]

    if state.execution_trace_path and Path(state.execution_trace_path).exists():
        try:
            trace_text = Path(state.execution_trace_path).read_text()
            unsoundness_lines = [l for l in trace_text.splitlines() if "[UNSOUNDNESS]" in l]
            if unsoundness_lines:
                lines.append("\n### [UNSOUNDNESS] lines found in previous execution trace (limited to 30):")
                lines += [f"  {l}" for l in unsoundness_lines[:min(30, len(unsoundness_lines))]]  # show up to 30 lines
            else:
                lines.append("\n### Previous execution trace: no [UNSOUNDNESS] lines found at all.")
        except Exception:
            pass

    if state.oracle_path and Path(state.oracle_path).exists():
        try:
            oracle = json.loads(Path(state.oracle_path).read_text())
            lines.append("\n### Oracle enclosing root cause methods (these MUST appear in the trace):")
            for rc_key, rc_val in oracle.items():
                method = rc_val.get("method", "?")
                lines.append(f"{method}")

            lines.append(f"This oracle is for the test case at path (ONLY see the /src directory of the test case if needed): {state.reproducing_test_case_path}")
        except Exception:
            pass

    if state.annotated_file and state.injected_at_line:
        lines.append("\n### Previous annotation was injected at this location:")
        lines.append(f"File Path: {state.annotated_file}")
        lines.append(f"Injected at line: {state.injected_at_line}")

    lines += [
        "\n### Required log format (the trace checker parses this):",
        f"  [UNSOUNDNESS] <pattern_class> | caller=<caller_method_fullname> | site=<site>",
        "\nRevise the injection point so the log fires with the correct caller format.",
    ]

    return "\n".join(lines)
