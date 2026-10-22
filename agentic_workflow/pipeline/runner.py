"""
pipeline/runner.py
==================
run_pipeline(): wires the stages together for one boundary, with the outer
retry loop (stages 3–6), the oracle refinement sub-loop and the inner
annotation loop (stages 7–8), and writes the final report.
"""

import logging

from pipeline.state import PipelineState
from pipeline.results import write_final_report, print_success_summary, print_failure_summary
from pipeline.stages import (
    stage_generate_report,
    stage_generate_test_case,
    stage_generate_oracle,
    stage_check_oracle_on_program,
    stage_check_reproduction,
    stage_root_cause_annotation,
    stage_check_trace,
    stage_refine_report,
)
from pipeline.stages.oracle import build_oracle_feedback_section
from config import MAX_LOOPS, ANNOTATION_MAX_RETRIES

log = logging.getLogger("pipeline")


# ── Per-edge file logging ──────────────────────────────────────────────────────

class _EdgeLogFilter(logging.Filter):
    """Passes only records whose message contains this edge's label."""
    def __init__(self, label: str):
        super().__init__()
        self._label = label

    def filter(self, record: logging.LogRecord) -> bool:
        return self._label in record.getMessage()

# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(state: PipelineState) -> None:
    state.output_dir.mkdir(parents=True, exist_ok=True)
    log_path     = state.output_dir / "pipeline.log"
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    file_handler.addFilter(_EdgeLogFilter(state.label()))
    logging.getLogger("pipeline").addHandler(file_handler)

    try:
        await _run_pipeline_inner(state)
    finally:
        logging.getLogger("pipeline").removeHandler(file_handler)
        file_handler.close()

async def _run_pipeline_inner(state: PipelineState) -> None:
    log.info(f"{state.label()} {'=' * 50}")
    log.info(f"{state.label()} PIPELINE START  {state.boundary_id}")
    log.info(f"{state.label()} {'=' * 50}")

    # Stages 3–8: main retry loop (each PipelineState holds exactly one boundary entry)
    while state.loop_count <= MAX_LOOPS:
        state.report_oracle_failure = False
        state.oracle_refine_count = 0
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

        # Stage 5: Generate oracle
        if not await stage_generate_oracle(state):
            write_final_report(state, success=False, reason="Oracle generation failed.")
            return

        # Stage 5b: Check oracle on real program
        oracle_prog_ok, oracle_prog_feedback = await stage_check_oracle_on_program(state)
        if not oracle_prog_ok:
            # Oracle refinement sub-loop: try refining instrumentation up to 2 times
            # before escalating to the report generator.
            for _ in range(2):
                instr_fb = build_oracle_feedback_section(state, oracle_prog_feedback)
                state.oracle_feedback.append(instr_fb)
                state.oracle_refine_count += 1
                log.info(f"{state.label()} Oracle refinement attempt {state.oracle_refine_count}/2")
                if not await stage_generate_oracle(state, refine=True):
                    break  # oracle generation itself failed; escalate
                oracle_prog_ok, oracle_prog_feedback = await stage_check_oracle_on_program(state)
                if oracle_prog_ok:
                    break  # refinement succeeded; fall through to Stage 6

            if not oracle_prog_ok:
                # All refinement attempts exhausted — escalate to report generator with vague feedback.
                state.reproduce_feedback.append(
                    "The root cause hypothesis is not correct. "
                    "Please refine your root cause analysis and reconsider what static analysis "
                    "behaviour leads to this boundary being missed."
                )
                state.oracle_feedback.clear()
                state.oracle_refine_count = 0
                state.report_oracle_failure = True
                state.report_refine_reason = "oracle_failure"
                log.info(f"{state.label()} Oracle refinement exhausted. Escalating to report generator.")
                if state.loop_count >= MAX_LOOPS:
                    break
                state.loop_count += 1
                continue  # → back to Stage 3 with vague feedback

        # Stage 6: Check reproduction
        reproduced, repr_feedback = await stage_check_reproduction(state)
        if not reproduced:
            state.reproduce_feedback.append(repr_feedback)
            state.report_refine_reason = "not_reproduced"
            log.info(f"{state.label()} Not reproduced. Feedback recorded.")
            if state.loop_count >= MAX_LOOPS:
                break
            state.loop_count += 1
            continue   # → back to stage 3 with reproduce_feedback injected

        log.info(f"{state.label()} Reproduced. Proceeding to annotation...")

        # Stages 7–8: inner annotation retry loop
        # Retries only the annotation + trace steps; stages 3–6 are NOT re-run here.
        state.annot_attempt = 0
        while state.annot_attempt <= ANNOTATION_MAX_RETRIES:
            annot_label = (
                f"Annotation attempt {state.annot_attempt + 1}/{ANNOTATION_MAX_RETRIES + 1}"
            )
            log.info(f"{state.label()} {'·' * 30} {annot_label}")

            # Stage 7: Root cause annotation + run annotated SA tool
            annot_ok, annot_fb = await stage_root_cause_annotation(state)
            if not annot_ok:
                state.trace_feedback.append(annot_fb)
                log.info(f"{state.label()} Annotation failed. Feedback recorded.")
                if state.annot_attempt >= ANNOTATION_MAX_RETRIES:
                    break
                state.annot_attempt += 1
                continue   # retry annotation only — stages 3–6 untouched

            # Stage 8: Check execution trace
            trace_ok, trace_fb = await stage_check_trace(state)
            if trace_ok:
                # Stage 9: Refine report with confirmed trace evidence
                await stage_refine_report(state)
                # Stage 10: Generalize instrumentation oracle
                # await stage_generalize_instrumentation(state)
                write_final_report(
                    state,
                    success=True,
                    reason="Root cause identified and confirmed via execution trace.",
                )
                print_success_summary(state)
                return

            state.trace_feedback.append(trace_fb)
            log.info(f"{state.label()} Trace check failed. Feedback recorded.")
            if state.annot_attempt >= ANNOTATION_MAX_RETRIES:
                break
            state.annot_attempt += 1
            # Loop back to stage 7 with updated trace_feedback — stages 3–6 untouched

        # Annotation retries exhausted — propagate to outer loop or fail
        if state.loop_count >= MAX_LOOPS:
            break
        state.loop_count += 1
        state.report_refine_reason = "trace_failure"
        # → back to stage 3 with all feedback injected (rare: only if outer loop allows it)

    # Max loops reached
    write_final_report(
        state,
        success=False,
        reason=(
            f"Exceeded maximum attempts (outer loops: {state.loop_count}, "
            f"annotation attempts: {state.annot_attempt}). "
            f"Reproduce feedbacks: {state.reproduce_feedback}. "
            f"Trace feedbacks: {state.trace_feedback}."
        ),
    )
    print_failure_summary(state)
