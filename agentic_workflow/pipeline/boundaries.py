"""
pipeline/boundaries.py
======================
Building PipelineStates from boundaries.json and filtering the queue on resume.
"""

import json
import sys
import logging
from pathlib import Path
from typing import Optional

from pipeline.state import PipelineState
from config import OUT_PIPELINE_RESULT, OUT_PRUNING_REPORT

log = logging.getLogger("pipeline")


# ── Boundary ID ───────────────────────────────────────────────────────────────

def _boundary_id(boundary: dict, index: int) -> str:
    """
    Derive a human-readable ID from a boundary entry.
    Used for directory naming and log labels.
    Example: "Handler.dispatch__ConcreteHandler.handlePost"
    """
    caller = boundary.get("caller", {})
    missed = boundary.get("missedCallee", {})

    def short(cls: str) -> str:
        return cls.rstrip(";").split("/")[-1].lstrip("L") if cls else "?"

    caller_label = f"{short(caller.get('declaringClass', ''))}.{caller.get('name', '?')}"
    missed_label = f"{short(missed.get('declaringClass', ''))}.{missed.get('name', '?')}"
    safe = f"{caller_label}__{missed_label}".replace("/", ".").replace(" ", "_")
    return f"b{index}_{safe}"

# ── State factory ─────────────────────────────────────────────────────────────

def make_state(
    program_name:    str,
    tool_name:       str,
    algorithm:       str,
    target_program:  str,
    main_class:      str,
    sa_tool:         str,
    boundary:        dict,
    boundary_index:  int,
    base_output_dir: Path,
    program_jar_path: Optional[str] = None,
) -> PipelineState:
    """
    Build a PipelineState with an isolated output directory.
    Output path: <base>/<program>/<tool>/<algorithm>/<boundary_id>/
    """
    b_id       = _boundary_id(boundary, boundary_index)
    output_dir = base_output_dir / program_name / tool_name / algorithm / b_id
    output_dir.mkdir(parents=True, exist_ok=True)

    state = PipelineState(
        program_name=program_name,
        tool_name=tool_name,
        algorithm=algorithm,
        boundary_index=boundary_index,
        boundary_id=b_id,
        target_program_path=target_program,
        main_class=main_class,
        sa_tool_path=sa_tool,
        boundary=boundary,
        output_dir=output_dir,
    )
    state.program_jar_path = program_jar_path
    return state

# ── Boundaries loader ─────────────────────────────────────────────────────────

def load_boundaries(path: str) -> list[dict]:
    """Load and return the list of boundary entries from a boundaries.json file."""
    p = Path(path)
    if not p.exists():
        log.error(f"Boundaries file not found: {path}")
        sys.exit(1)
    with open(p) as f:
        data = json.load(f)
    entries = data.get("boundaries", [])
    if not entries:
        log.error(f"No boundary entries found in: {path}")
        # sys.exit(1)
    log.info(f"Loaded {len(entries)} boundary entries from: {path}")
    return entries

# ── Resume filter (same output dir) ───────────────────────────────────────────

def filter_resume(
    output_dir:     Path,
    program_queues: dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """
    Resume an interrupted run by pruning the freshly-built queue against an
    existing output directory. Two passes, mutating program_queues in place:

      A. Drop any state already analyzed/attempted — its
         output_dir/pipeline_result.json exists (regardless of success).
      B. Drop any state already deduplicated — listed in the pruned_boundaries
         of some prior boundary whose pipeline_result.json has success == true
         and that has a pruning_report.json. Matched by (program_name, boundary_id).

    Read-only w.r.t. disk; fail-open on unreadable files.
    """
    total_before = sum(len(q) for _, q in program_queues.values())

    # ── Pass A: already analyzed/attempted (own result file exists) ──
    analyzed = 0
    for program_key, (analysis, states) in program_queues.items():
        kept: list[PipelineState] = []
        for s in states:
            if (s.output_dir / OUT_PIPELINE_RESULT).exists():
                analyzed += 1
            else:
                kept.append(s)
        program_queues[program_key] = (analysis, kept)

    # ── Pass B: already deduplicated (listed in a successful pruning report) ──
    pruned_ids: set[tuple[str, str]] = set()  # (program_name, boundary_id)
    for prev_boundary_dir in sorted(output_dir.glob("*/*/*/*")):
        if not prev_boundary_dir.is_dir():
            continue

        report_path = prev_boundary_dir / OUT_PRUNING_REPORT
        if not report_path.exists():
            continue

        # Gate on success == true from the triggering boundary's result.
        result_path = prev_boundary_dir / OUT_PIPELINE_RESULT
        try:
            success = json.loads(result_path.read_text()).get("success", False)
        except Exception:
            success = False
        if not success:
            continue

        try:
            report_data = json.loads(report_path.read_text())
        except Exception as e:
            log.warning(f"Resume filter: failed to read {report_path}: {e} — skipping")
            continue

        for entry in report_data.get("pruned_boundaries", []):
            prog = entry.get("program_name")
            b_id = entry.get("boundary_id")
            if prog and b_id:
                pruned_ids.add((prog, b_id))

    deduplicated = 0
    if pruned_ids:
        for program_key, (analysis, states) in program_queues.items():
            kept = []
            for s in states:
                if (s.program_name, s.boundary_id) in pruned_ids:
                    deduplicated += 1
                else:
                    kept.append(s)
            program_queues[program_key] = (analysis, kept)

    total_after = sum(len(q) for _, q in program_queues.values())
    log.info(
        f"Resume filter: {total_before} → {total_after} boundaries remaining "
        f"({analyzed} already-analyzed, {deduplicated} deduplicated)"
    )
