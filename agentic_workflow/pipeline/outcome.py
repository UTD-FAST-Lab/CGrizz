"""
pipeline/outcome.py
===================
pipeline_result.json (the authoritative per-boundary status) and the
success / failure summaries printed at the end of a run.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from pipeline.state import PipelineState
from config import OUT_PIPELINE_RESULT

log = logging.getLogger("pipeline")


# ── Final report ──────────────────────────────────────────────────────────────


def write_final_report(state: PipelineState, success: bool, reason: str,
                       failure_kind: Optional[str] = None) -> None:
    """
    Write pipeline_result.json to the output directory.
    Always called at the end of the pipeline, whether success or failure.

    failure_kind separates failures that say nothing about the hypothesis
    ("instrumentation_failure", "oracle_unsupported") from the others (None).
    """
    report = {
        "timestamp":  datetime.now().isoformat(),
        "success":    success,
        "loops_used": state.loop_count,
        "reason":     reason,
        "failure_kind": failure_kind,
        "identity": {
            "program_name":    state.program_name,
            "tool_name":       state.tool_name,
            "algorithm":       state.algorithm,
            "boundary_index":  state.boundary_index,
            "boundary_id":     state.boundary_id,
        },
        "artifacts": {
            "unsoundness_report":        state.unsoundness_report,
            "reproducing_test_case":     state.reproducing_test_case_path,
            "non_reproducing_test_case": state.non_reproducing_test_case_path,
            "staticcg":                  state.staticcg_path,
            "dyncg":                     state.dyncg_path,
            "boundary":                  state.boundary_path,
            "annotated_sa_tool":         state.annotated_sa_tool_path,
            "modified_analysis":         state.modified_analysis_path,
            "oracle_spec":               state.oracle_spec_path,
            "target_oracle":             state.target_oracle_path,
        },
        "oracle_verdicts": state.oracle_verdicts,
        "feedback_history": {
            "reproduce_feedback": state.reproduce_feedback,
            "trace_feedback":     state.trace_feedback,
        },
        "report_history": state.report_history,
    }

    out_path = state.output_dir / OUT_PIPELINE_RESULT
    out_path.write_text(json.dumps(report, indent=2))
    log.info(f"{state.label()} Result → {out_path}")

# ── Summary printers ──────────────────────────────────────────────────────────


def print_success_summary(state: PipelineState) -> None:
    from config import MAX_LOOPS
    log.info(f"\n{state.label()} {'=' * 50}")
    log.info(f"{state.label()} SUCCESS  {state.boundary_id}")
    log.info(f"{state.label()} {'=' * 50}")
    log.info(f"{state.label()} Loops used     : {state.loop_count}/{MAX_LOOPS}")
    log.info(f"{state.label()} Report         : {state.unsoundness_report}")
    log.info(f"{state.label()} Reproducing TC : {state.reproducing_test_case_path}")
    log.info(f"{state.label()} Non-repro TC   : {state.non_reproducing_test_case_path}")
    log.info(f"{state.label()} Annotated tool : {state.annotated_sa_tool_path}")
    log.info(f"{state.label()} Modified output: {state.modified_analysis_path}")


def print_failure_summary(state: PipelineState) -> None:
    from config import MAX_LOOPS
    log.warning(f"\n{state.label()} {'=' * 50}")
    log.warning(f"{state.label()} FAILED  {state.boundary_id}")
    log.warning(f"{state.label()} {'=' * 50}")
    log.warning(f"{state.label()} Loops used : {state.loop_count}/{MAX_LOOPS}")
    for i, fb in enumerate(state.reproduce_feedback):
        log.warning(f"{state.label()} Reproduce [{i+1}]: {fb}")
    for i, fb in enumerate(state.trace_feedback):
        log.warning(f"{state.label()} Trace     [{i+1}]: {fb}")
