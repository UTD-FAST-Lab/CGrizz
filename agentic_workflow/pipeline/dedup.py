"""
pipeline/dedup.py
=================
Oracle-based deduplication: after a boundary succeeds, run its oracle on the
remaining programs and prune every boundary whose missed edge is a symptom
(an equivalence group), plus the pre-run variants driven by previous results.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.oracle import find_agent_jar, find_agent_jar_from_dir, run_oracle_on_program, extract_symptoms
from config import OUT_PIPELINE_RESULT, OUT_PRUNING_REPORT, OUT_TARGET_ORACLE

log = logging.getLogger("pipeline")


def prune_queue(
    states: list[PipelineState],
    symptoms: set[tuple[str, str, str, str]],
) -> tuple[list[PipelineState], list[PipelineState]]:
    """
    Partition states into (kept, pruned) based on symptom matching.
    A state is pruned if its boundary's caller→callee edge appears in symptoms.
    """
    kept:   list[PipelineState] = []
    pruned: list[PipelineState] = []

    for state in states:
        b = state.boundary
        caller_class  = b.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
        caller_method = b.get("caller", {}).get("name", "")
        callee_class  = b.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
        callee_method = b.get("missedCallee", {}).get("name", "")

        if (caller_class, caller_method, callee_class, callee_method) in symptoms:
            pruned.append(state)
        else:
            kept.append(state)

    return kept, pruned


def write_pruning_report(
    state:       PipelineState,
    pruned:      list[PipelineState],
    oracle_runs: list[dict],
) -> None:
    """Write pruning_report.json to the successful boundary's output directory."""
    try:
        report = {
            "timestamp": datetime.now().isoformat(),
            "triggered_by": {
                "boundary_id":  state.boundary_id,
                "program_name": state.program_name,
                "tool_name":    state.tool_name,
                "algorithm":    state.algorithm,
            },
            "oracle_runs":  oracle_runs,
            "pruned_count": len(pruned),
            "pruned_boundaries": [
                {
                    "boundary_id":    s.boundary_id,
                    "boundary_index": s.boundary_index,
                    "program_name":   s.program_name,
                    "caller_class":   s.boundary.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";"),
                    "caller_method":  s.boundary.get("caller", {}).get("name", ""),
                    "callee_class":   s.boundary.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";"),
                    "callee_method":  s.boundary.get("missedCallee", {}).get("name", ""),
                }
                for s in pruned
            ],
        }
        out_path = state.output_dir / OUT_PRUNING_REPORT
        out_path.write_text(json.dumps(report, indent=2))
        log.info(f"{state.label()} Pruning report → {out_path}")
    except Exception as e:
        log.warning(f"{state.label()} Failed to write pruning report: {e}")


def update_pruning_report_prededup(
    boundary_dir:    Path,
    identity:        dict,
    new_oracle_runs: list[dict],
    new_pruned:      list[dict],
) -> None:
    """
    Extend (or create) pruning_report.json in a previous boundary's output directory
    with results from a pre-run deduplication pass.
    Existing oracle_runs and pruned_boundaries are preserved; new entries are appended.
    New oracle_run entries carry "phase": "prededup" to distinguish them.
    """
    out_path = boundary_dir / OUT_PRUNING_REPORT
    try:
        if out_path.exists():
            report = json.loads(out_path.read_text())
            report.setdefault("oracle_runs", [])
            report.setdefault("pruned_boundaries", [])
            report["prededup_timestamp"] = datetime.now().isoformat()
        else:
            report = {
                "timestamp": datetime.now().isoformat(),
                "triggered_by": identity,
                "oracle_runs": [],
                "pruned_boundaries": [],
                "pruned_count": 0,
            }
        report["oracle_runs"].extend(new_oracle_runs)
        report["pruned_boundaries"].extend(new_pruned)
        report["pruned_count"] = len(report["pruned_boundaries"])
        out_path.write_text(json.dumps(report, indent=2))
        log.info(f"Pre-dedup pruning report updated → {out_path}")
    except Exception as e:
        log.warning(f"Failed to update pruning report at {out_path}: {e}")
# ── Oracle deduplication helpers ──────────────────────────────────────────────


def is_successful(state: PipelineState) -> bool:
    """Read pipeline_result.json and return the success flag."""
    result_path = state.output_dir / OUT_PIPELINE_RESULT
    try:
        return json.loads(result_path.read_text()).get("success", False)
    except Exception:
        return False


async def run_dedup(
    successful_state: PipelineState,
    program_queues:   dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """
    After a successful pipeline run, run the oracle agent against every program
    that still has remaining boundaries in the queue and prune matching entries.
    Fail-open: failures in any step are logged and skipped.
    """
    agent_jar = find_agent_jar(successful_state)
    if not agent_jar:
        log.warning(f"{successful_state.label()} Agent JAR not found — skipping dedup")
        return

    all_pruned:  list[PipelineState] = []
    oracle_runs: list[dict]          = []

    successful_key = (
        f"{successful_state.program_name}/"
        f"{successful_state.tool_name}/"
        f"{successful_state.algorithm}"
    )

    for program_key, (analysis, remaining) in program_queues.items():
        if not remaining:
            continue

        # For the successful state's own program, the oracle was already run
        # against the real program during the pipeline — reuse oracle_target.json
        # instead of re-running instrumentation.
        if program_key == successful_key:
            oracle_target = (
                Path(successful_state.target_oracle_path)
                if successful_state.target_oracle_path
                else successful_state.output_dir / OUT_TARGET_ORACLE
            )
            if oracle_target.exists():
                log.info(
                    f"{successful_state.label()} Reusing existing oracle_target.json "
                    f"for {analysis.get('program_name', program_key)} — skipping oracle run"
                )
                oracle_runs.append({
                    "program_name": analysis.get("program_name", program_key),
                    "oracle_path":  str(oracle_target),
                    "status":       "reused_target_oracle",
                })
                symptoms = extract_symptoms(oracle_target)
                if symptoms:
                    kept, pruned = prune_queue(remaining, symptoms)
                    program_queues[program_key] = (analysis, kept)
                    all_pruned.extend(pruned)
                    if pruned:
                        log.info(
                            f"{successful_state.label()} Pruned {len(pruned)} boundaries "
                            f"from {analysis['program_name']}: "
                            f"{[s.boundary_id for s in pruned]}"
                        )
            continue
            # No oracle_target.json on disk — fall through to running the oracle.

        jar_path   = analysis.get("program_jar_path")
        main_class = analysis.get("main_class")

        if not jar_path or not main_class:
            log.warning(
                f"{successful_state.label()} Missing program_jar_path or main_class "
                f"for {analysis.get('program_name', program_key)} — skipping oracle"
            )
            oracle_runs.append({
                "program_name": analysis.get("program_name", program_key),
                "oracle_path":  None,
                "status":       "skipped_no_config",
            })
            continue

        oracle_out = (
            successful_state.output_dir
            / "oracles" / f"oracle_{analysis['program_name']}.json"
        )
        oracle_out.parent.mkdir(parents=True, exist_ok=True)

        ok = await run_oracle_on_program(agent_jar, jar_path, main_class, oracle_out)
        oracle_runs.append({
            "program_name": analysis["program_name"],
            "oracle_path":  str(oracle_out),
            "status":       "ok" if ok else "failed",
        })

        if ok:
            symptoms = extract_symptoms(oracle_out)
            if symptoms:
                kept, pruned = prune_queue(remaining, symptoms)
                program_queues[program_key] = (analysis, kept)
                all_pruned.extend(pruned)
                if pruned:
                    log.info(
                        f"{successful_state.label()} Pruned {len(pruned)} boundaries "
                        f"from {analysis['program_name']}: "
                        f"{[s.boundary_id for s in pruned]}"
                    )

    if all_pruned:
        write_pruning_report(successful_state, all_pruned, oracle_runs)

# ── Pre-run deduplication ─────────────────────────────────────────────────────


def _state_to_pruned_dict(s: PipelineState) -> dict:
    return {
        "boundary_id":    s.boundary_id,
        "boundary_index": s.boundary_index,
        "program_name":   s.program_name,
        "caller_class":   s.boundary.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";"),
        "caller_method":  s.boundary.get("caller", {}).get("name", ""),
        "callee_class":   s.boundary.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";"),
        "callee_method":  s.boundary.get("missedCallee", {}).get("name", ""),
    }


async def run_prededup(
    prev_results_dirs: list[Path],
    program_queues:    dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """
    Before the main sequential loop, run every instrumentation JAR found in
    each of prev_results_dirs against each program in program_queues and prune
    matching boundaries.  Oracle outputs go to <prev_boundary_dir>/oracles_prededup/.
    Pruning results are appended to the previous boundary's pruning_report.json.
    Fail-open: any missing JAR, config, or oracle failure is logged and skipped.
    """
    total_before = sum(len(q) for _, q in program_queues.values())

    prev_boundary_dirs = [
        d
        for prev_results_dir in prev_results_dirs
        for d in sorted(prev_results_dir.glob("*/*/*/*"))
    ]

    for prev_boundary_dir in prev_boundary_dirs:
        if not prev_boundary_dir.is_dir():
            continue

        agent_jar = find_agent_jar_from_dir(prev_boundary_dir)
        if not agent_jar:
            continue

        # Read identity from pipeline_result.json; fall back to path structure.
        result_path = prev_boundary_dir / OUT_PIPELINE_RESULT

        if not result_path.exists():
            log.debug(f"Pre-dedup: no pipeline_result.json in {prev_boundary_dir} — skipping")
            continue

        identity: dict = {}
        if result_path.exists():
            try:
                result_data = json.loads(result_path.read_text())
                success = result_data.get("success", False)
                if not success:
                    log.debug(
                        f"Pre-dedup: skipping {prev_boundary_dir} "
                        f"(pipeline_result.json has success == false)"
                    )
                    continue
                identity = result_data.get("identity", {})
            except Exception:
                pass
        if not identity.get("program_name"):
            parts = prev_boundary_dir.parts
            identity = {
                "program_name":   parts[-4],
                "tool_name":      parts[-3],
                "algorithm":      parts[-2],
                "boundary_id":    parts[-1],
                "boundary_index": 0,
            }

        log.info(
            f"Pre-dedup: running instrumentation from "
            f"{identity.get('boundary_id', prev_boundary_dir.name)}"
        )

        oracle_runs:  list[dict] = []
        pruned_dicts: list[dict] = []

        for program_key, (analysis, remaining) in program_queues.items():
            if not remaining:
                continue

            jar_path   = analysis.get("program_jar_path")
            main_class = analysis.get("main_class")

            if not jar_path or not main_class:
                log.warning(
                    f"Pre-dedup: missing program_jar_path or main_class for "
                    f"{analysis.get('program_name', program_key)} — skipping"
                )
                oracle_runs.append({
                    "program_name": analysis.get("program_name", program_key),
                    "oracle_path":  None,
                    "status":       "skipped_no_config",
                    "phase":        "prededup",
                })
                continue

            oracle_out = (
                prev_boundary_dir
                / "oracles_prededup"
                / f"oracle_{analysis['program_name']}.json"
            )
            oracle_out.parent.mkdir(parents=True, exist_ok=True)

            ok, _, _, _ = await run_oracle_on_program(agent_jar, jar_path, main_class, oracle_out)
            oracle_runs.append({
                "program_name": analysis["program_name"],
                "oracle_path":  str(oracle_out),
                "status":       "ok" if ok else "failed",
                "phase":        "prededup",
            })

            if ok:
                symptoms = extract_symptoms(oracle_out)
                if symptoms:
                    kept, pruned = prune_queue(remaining, symptoms)
                    program_queues[program_key] = (analysis, kept)
                    pruned_dicts.extend(_state_to_pruned_dict(s) for s in pruned)
                    if pruned:
                        log.info(
                            f"Pre-dedup: pruned {len(pruned)} boundaries from "
                            f"{analysis['program_name']}: "
                            f"{[s.boundary_id for s in pruned]}"
                        )

        update_pruning_report_prededup(prev_boundary_dir, identity, oracle_runs, pruned_dicts)

    total_after = sum(len(q) for _, q in program_queues.values())
    log.info(
        f"Pre-dedup complete: {total_before} → {total_after} boundaries remaining "
        f"({total_before - total_after} pruned)"
    )

# ── Report-based pre-dedup (no instrumentation) ───────────────────────────────


def run_report_prededup(
    prev_results_dirs: list[Path],
    program_queues:    dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """Prune the queue using pruned_boundaries from existing pruning_report.json files.
    No instrumentation is run; this is a read-only, fast alternative to run_prededup."""
    total_before = sum(len(q) for _, q in program_queues.values())

    # Build index: program_name → program_key for O(1) lookup
    program_name_to_key: dict[str, str] = {
        analysis["program_name"]: pk
        for pk, (analysis, _) in program_queues.items()
    }

    prev_boundary_dirs = [
        d
        for prev_results_dir in prev_results_dirs
        for d in sorted(prev_results_dir.glob("*/*/*/*"))
    ]

    for prev_boundary_dir in prev_boundary_dirs:
        if not prev_boundary_dir.is_dir():
            continue

        report_path = prev_boundary_dir / OUT_PRUNING_REPORT
        if not report_path.exists():
            log.debug(f"Report-prededup: no pruning_report.json in {prev_boundary_dir} — skipping")
            continue

        result_path = prev_boundary_dir / OUT_PIPELINE_RESULT
        if not result_path.exists():
            log.debug(f"Report-prededup: no pipeline_result.json in {prev_boundary_dir} — skipping")
            continue

        try:
            success = json.loads(result_path.read_text()).get("success", False)
            if not success:
                log.debug(
                    f"Report-prededup: skipping {prev_boundary_dir} "
                    f"(pipeline_result.json has success == false)"
                )
                continue
            report_data = json.loads(report_path.read_text())
        except Exception as e:
            log.warning(f"Report-prededup: failed to read {report_path}: {e} — skipping")
            continue

        pruned_boundaries = report_data.get("pruned_boundaries", [])
        if not pruned_boundaries:
            continue

        # Group symptoms by program_name
        symptoms_by_program: dict[str, set] = {}
        for entry in pruned_boundaries:
            prog = entry.get("program_name")
            cc = entry.get("caller_class",  "").replace("/", ".")
            cm = entry.get("caller_method", "")
            ec = entry.get("callee_class",  "").replace("/", ".")
            em = entry.get("callee_method", "")
            if prog and cc and cm and ec and em:
                symptoms_by_program.setdefault(prog, set()).add((cc, cm, ec, em))

        for prog_name, symptoms in symptoms_by_program.items():
            program_key = program_name_to_key.get(prog_name)
            if program_key is None:
                log.debug(f"Report-prededup: '{prog_name}' not in current queues — skipping")
                continue

            analysis, remaining = program_queues[program_key]
            if not remaining:
                continue

            kept, pruned = prune_queue(remaining, symptoms)
            program_queues[program_key] = (analysis, kept)

            if pruned:
                log.info(
                    f"Report-prededup: pruned {len(pruned)} from '{prog_name}' "
                    f"via {prev_boundary_dir.name}: {[s.boundary_id for s in pruned]}"
                )

    total_after = sum(len(q) for _, q in program_queues.values())
    log.info(
        f"Report-prededup complete: {total_before} → {total_after} boundaries remaining "
        f"({total_before - total_after} pruned)"
    )
