"""
pipeline/stages/annotation.py
==============================
Stage 7 (annotate the SA tool with one [UNSOUNDNESS] log and run it) and
stage 8 (check the execution trace: the log fires for the root-cause methods,
only for a small share of the analysed methods, and not for the
non-reproducing test case — see pipeline/warning_checks.py).
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.agents import build_agent_options
from pipeline.analysis import run_annotated_sa
from pipeline.agent_runner import run_agent, parse_json_from_response
from pipeline.edges import is_reachable, rc_in_trace
from pipeline.edge_mapping import map_files
from pipeline.warning_checks import (
    check_coverage,
    check_description,
    check_location,
    check_negative,
    edge_warning_lines,
    check_selectivity,
    warning_callers,
    warning_lines,
)
from config import (
    OUT_ANNOTATED_TOOL_DIR,
    OUT_EXECUTION_TRACE,
    OUT_STATICCG_DIR,
    ANNOTATION_MAX_CALLER_FRACTION,
    ANNOTATION_NEGATIVE_CHECK,
    ANNOTATION_MAX_DESCRIPTION_CHARS,
    ANNOTATION_REQUIRE_EDGE_COVERAGE,
    TOOL_MARKERS,
)
from pipeline import tool_markers

log = logging.getLogger("pipeline")


# ── Stage 7: Root cause annotation ────────────────────────────────────────────


@timed("annotation")
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

    # Unsoundness-marker module of this tool, if there is one (issue #14). Idempotent.
    if TOOL_MARKERS and tool_markers.spec(state.tool_name):
        try:
            changed = tool_markers.install(state.tool_name, annotated_out)
            state.tool_markers_installed = True
            log.info(f"{state.label()} Tool marker module installed ({len(changed)} files).")
        except tool_markers.ToolMarkerError as e:
            log.warning(f"{state.label()} Tool marker module not installed: {e}")

    annot_feedback = _build_annotation_feedback(state)
    markers = _marker_section(state)

    annot_response = await run_agent(
        prompt=f"""\
Use the root-cause-annotator agent to annotate the SA tool for this boundary.
Invoke it via the Task tool.

## Unsoundness report

{state.unsoundness_report}

## SA tool source

{annotated_out}/
{markers}{annot_feedback}

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


@timed("trace_check")
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
        return False, ("[annotation / trace check] The annotated tool analysed the reproducing test case and printed "
                       "no [UNSOUNDNESS] line: the log is on a path the tool does not take for this test case, or its "
                       "guard never holds. Move it to where the tool processes the root-cause instruction "
                       f"({state.annotated_file or 'see toolCodeEvidence'}).")

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
            printed = ", ".join(sorted(set(warning_callers(unsoundness_lines)))[:5]) or "none"
            return False, (f"[annotation / trace check] The [UNSOUNDNESS] log printed {len(unsoundness_lines)} lines, "
                           f"but none with caller= naming the root-cause method {method} (callers printed: {printed}). "
                           "Either the log is in code that analyses other methods, or caller= is formatted wrongly "
                           "(it must be package.Class.method(params)).")

    # The warning must say what went wrong, briefly, and be specific to the pattern (issue #4).
    ok, feedback = check_description(unsoundness_lines, ANNOTATION_MAX_DESCRIPTION_CHARS)
    if not ok:
        log.error(f"{state.label()} {feedback.splitlines()[0]}")
        return False, feedback

    edge_lines = edge_warning_lines(trace_log)
    for check in (lambda: check_description(edge_lines, ANNOTATION_MAX_DESCRIPTION_CHARS),
                  lambda: check_location(unsoundness_lines + edge_lines),
                  lambda: check_coverage(trace_log, state.boundary_path, Path(state.reproducing_test_case_path))
                  if state.tool_markers_installed and ANNOTATION_REQUIRE_EDGE_COVERAGE else (True, "")):
        ok, feedback = check()
        if not ok:
            log.error(f"{state.label()} {feedback.splitlines()[0]}")
            return False, feedback

    ok, feedback = check_selectivity(unsoundness_lines, state.staticcg_path, ANNOTATION_MAX_CALLER_FRACTION)
    if not ok:
        log.error(f"{state.label()} {feedback.splitlines()[0]}")
        return False, feedback

    if ANNOTATION_NEGATIVE_CHECK and state.non_reproducing_test_case_path \
            and Path(state.non_reproducing_test_case_path).exists():
        non_repro = Path(state.non_reproducing_test_case_path)
        run_annotated_sa(state, project="non_reproducing", test_case_path=str(non_repro))
        negative_log = non_repro / OUT_STATICCG_DIR / OUT_EXECUTION_TRACE
        try:
            negative_text = negative_log.read_text()
            negative_lines = warning_lines(negative_text) + edge_warning_lines(negative_text)
        except Exception as e:
            log.warning(f"{state.label()} Negative check skipped — could not read {negative_log}: {e}")
        else:
            state.negative_trace_path = str(negative_log)
            ok, feedback = check_negative(negative_lines, non_repro)
            if not ok:
                log.error(f"{state.label()} {feedback.splitlines()[0]}")
                return False, feedback
            log.info(f"{state.label()} Negative check passed — no [UNSOUNDNESS] log for the non-reproducing test case.")

    # Which missed edges of the test case the warnings point at (issue #14). Recorded, not enforced:
    # a warning at the root cause covers only edges missed at that instruction.
    if state.boundary_path:
        mapping = map_files([trace_log_path], state.boundary_path)
        state.warning_coverage = {
            **mapping["counts"],
            "explained_edges": mapping["explained"][:20],
            "boundaries_without_warning": mapping["boundary_only"][:20],
            "warnings_without_boundary": mapping["warning_only"][:20],
        }
        log.info(f"{state.label()} Warning coverage on the test case: {mapping['counts']}")

    log.info(f"{state.label()} Trace check passed — expected logs found in execution trace.")
    return True, "Trace check passed — expected logs found in execution trace."


def _marker_section(state: PipelineState) -> str:
    """How to use the tool's unsoundness-marker module, when it is installed (issue #14)."""
    usage = tool_markers.usage_file(state.tool_name) if state.tool_markers_installed else None
    if not usage:
        return ""
    return (f"\n## Unsoundness markers for {state.tool_name}\n\n"
            f"A marker module is installed in the tool. Read {usage} and, when the root cause loses a value that "
            "later reaches calls, mark it at the give-up point so the tool warns at every affected call "
            "([UNSOUNDNESS-EDGE]). The pipeline checks that every missed edge of the test case gets a warning.\n")


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
            edges = edge_warning_lines(trace_text)
            if edges:
                lines.append(f"\n### [UNSOUNDNESS-EDGE] lines in the previous trace ({len(edges)}, limited to 10):")
                lines += [f"  {l}" for l in edges[:10]]
            if unsoundness_lines:
                lines.append("\n### [UNSOUNDNESS] lines found in previous execution trace (limited to 30):")
                lines += [f"  {l}" for l in unsoundness_lines[:min(30, len(unsoundness_lines))]]  # show up to 30 lines
            else:
                lines.append("\n### Previous execution trace: no [UNSOUNDNESS] lines found at all.")
        except Exception:
            pass

    if state.negative_trace_path and Path(state.negative_trace_path).exists():
        try:
            negative = warning_lines(Path(state.negative_trace_path).read_text())
            if negative:
                lines.append("\n### [UNSOUNDNESS] lines on the NON-reproducing test case (must be none for its own classes; limited to 15):")
                lines += [f"  {l}" for l in negative[:15]]
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
        "  [UNSOUNDNESS] <very short problem description> | caller=<caller_method_fullname> | site=<site> | line=<line> | pc=<pc>",
        "\nRevise the injection point so the log fires with the correct caller format.",
    ]

    return "\n".join(lines)
