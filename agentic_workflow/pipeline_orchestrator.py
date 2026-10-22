"""
pipeline_orchestrator.py
========================
Entry point for the unsoundness analysis pipeline.

Input
─────
A boundaries.json file per (program, SA tool, algorithm) triple.
Each boundary entry in the file becomes one pipeline run.

Three execution modes
─────────────────────
  single         One analysis entry, one boundary entry (by index).
                 Uses flat CLI flags.

  parallel-edges One analysis entry from config, all its boundary
                 entries run sequentially with oracle deduplication.

  parallel-all   All analysis entries × all their boundary entries,
                 processed sequentially with oracle deduplication.

Usage examples
──────────────
  # single — boundary index 0 of program_A / WALA / 0-CFA
  python pipeline_orchestrator.py single \\
      --program-name   program_A \\
      --target-program ./programs/A \\
      --main-class      com.example.Main \\
      --sa-tool-name   WALA \\
      --sa-algorithm   0-CFA \\
      --sa-tool        ./tools/wala \\
      --boundaries     ./input_data/program_A/WALA/0-CFA/boundaries.json \\
      --boundary-index 0 \\
      --output-dir     ./results

  # parallel-edges — all boundaries for one analysis entry (sequential + dedup)
  python pipeline_orchestrator.py parallel-edges \\
      --config       ./pipeline_config.json \\
      --program-name program_A \\
      --tool-name    WALA \\
      --algorithm    0-CFA \\
      --output-dir   ./results

  # parallel-all — everything in the config (sequential + dedup)
  python pipeline_orchestrator.py parallel-all \\
      --config      ./pipeline_config.json \\
      --output-dir  ./results
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.runner import run_pipeline
from pipeline.outcome import write_final_report
from pipeline.boundaries import make_state, load_boundaries, filter_resume
from pipeline.dedup import is_successful, run_dedup, run_prededup, run_report_prededup
from pipeline.oracle import ensure_agent_built
from pipeline.sampler import (
    filter_jvm_runtime_boundaries,
    filter_jdk_caller_boundaries,
)

log = logging.getLogger("pipeline.orchestrator")


# ── Sequential pipeline runner ────────────────────────────────────────────────


async def _run_sequential(
    program_queues: dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """
    Process all states across all program queues sequentially.
    After each successful run, execute the oracle deduplication step against
    all programs that still have remaining boundaries.
    """
    # Build a flat ordered list of (program_key, state).
    # States are visited in FIFO order across all programs.
    flat = [
        (pk, s)
        for pk, (_, states) in program_queues.items()
        for s in states
    ]

    processed: set[str] = set()

    for program_key, state in flat:
        if state.boundary_id in processed:
            continue  # already handled or pruned after flat list was built

        # Check whether this state is still in the program's remaining queue
        _, remaining = program_queues[program_key]
        if state not in remaining:
            continue  # was pruned by a previous dedup step

        remaining.remove(state)
        processed.add(state.boundary_id)

        total_remaining = sum(len(q) for _, q in program_queues.values())
        log.info(f"{state.label()} Processing ({total_remaining} remaining in queue)")

        try:
            await run_pipeline(state)
        except Exception as e:
            log.error(f"{state.label()} Unexpected pipeline error: {e}")
            write_final_report(state, success=False, reason=f"Unexpected pipeline error: {e}")
            continue

        # if is_successful(state) or find_agent_jar(state) is not None:
        if is_successful(state):
            log.info(
                f"{state.label()} {'Success' if is_successful(state) else 'Oracle JAR available'}"
                f" — running oracle dedup"
            )
            await run_dedup(state, program_queues)


# ── Mode 1: single ────────────────────────────────────────────────────────────


def run_single(args: argparse.Namespace) -> None:
    """One analysis entry, one boundary entry by index. No dedup."""
    boundaries = load_boundaries(args.boundaries)

    if args.boundary_index >= len(boundaries):
        log.error(
            f"--boundary-index {args.boundary_index} is out of range "
            f"(file has {len(boundaries)} entries, indices 0–{len(boundaries)-1})."
        )
        sys.exit(1)

    boundary = boundaries[args.boundary_index]
    state    = make_state(
        program_name=args.program_name,
        tool_name=args.sa_tool_name,
        algorithm=args.sa_algorithm,
        target_program=args.target_program,
        main_class=args.main_class,
        sa_tool=args.sa_tool,
        boundary=boundary,
        boundary_index=args.boundary_index,
        base_output_dir=Path(args.output_dir),
        program_jar_path=args.program_jar_path
    )

    log.info(f"Mode: single | {state.label()}")
    asyncio.run(run_pipeline(state))


# ── Mode 2: parallel-edges ────────────────────────────────────────────────────


def run_parallel_edges(args: argparse.Namespace) -> None:
    """One analysis entry from config, all boundaries processed sequentially with dedup."""
    analysis       = _find_analysis(args)
    all_boundaries = load_boundaries(analysis["boundaries_file"])

    jvm_filtered, jvm_indices   = filter_jvm_runtime_boundaries(all_boundaries)
    boundaries,   local_indices = filter_jdk_caller_boundaries(jvm_filtered)
    original_indices            = [jvm_indices[i] for i in local_indices]

    states = [
        make_state(
            program_name=analysis["program_name"],
            tool_name=analysis["sa_tool_name"],
            algorithm=analysis["sa_algorithm"],
            target_program=analysis["target_program"],
            main_class=analysis.get("main_class"),
            sa_tool=analysis["sa_tool"],
            boundary=b,
            boundary_index=idx,
            base_output_dir=Path(args.output_dir),
            program_jar_path=analysis.get("program_jar_path"),
        )
        for b, idx in zip(boundaries, original_indices)
    ]

    program_key = (
        f"{analysis['program_name']}/"
        f"{analysis['sa_tool_name']}/"
        f"{analysis['sa_algorithm']}"
    )
    program_queues = {program_key: (analysis, list(states))}

    log.info(
        f"Mode: parallel-edges (sequential+dedup) | "
        f"{program_key} | "
        f"boundaries: {len(states)} (after filtering from {len(all_boundaries)})"
    )

    if Path(args.output_dir).exists():
        filter_resume(Path(args.output_dir), program_queues)

    if args.prev_results:
        prev_dirs = [Path(p) for p in args.prev_results]
        if args.report_prededup:
            run_report_prededup(prev_dirs, program_queues)
        else:
            asyncio.run(run_prededup(prev_dirs, program_queues))
    asyncio.run(_run_sequential(program_queues))


# ── Mode 3: parallel-all ──────────────────────────────────────────────────────


def run_parallel_all(args: argparse.Namespace) -> None:
    """All analysis entries × all boundary entries, processed sequentially with dedup."""
    config = _load_config(args.config)

    program_queues: dict[str, tuple[dict, list[PipelineState]]] = {}

    for analysis in config["analyses"]:
        all_boundaries = load_boundaries(analysis["boundaries_file"])

        jvm_filtered, jvm_indices   = filter_jvm_runtime_boundaries(all_boundaries)
        boundaries,   local_indices = filter_jdk_caller_boundaries(jvm_filtered)
        original_indices            = [jvm_indices[i] for i in local_indices]

        states = [
            make_state(
                program_name=analysis["program_name"],
                tool_name=analysis["sa_tool_name"],
                algorithm=analysis["sa_algorithm"],
                target_program=analysis["target_program"],
                main_class=analysis.get("main_class"),
                sa_tool=analysis["sa_tool"],
                boundary=b,
                boundary_index=idx,
                base_output_dir=Path(args.output_dir),
                program_jar_path=analysis.get("program_jar_path"),
            )
            for b, idx in zip(boundaries, original_indices)
        ]

        program_key = (
            f"{analysis['program_name']}/"
            f"{analysis['sa_tool_name']}/"
            f"{analysis['sa_algorithm']}"
        )
        program_queues[program_key] = (analysis, states)

    total = sum(len(q) for _, q in program_queues.values())
    log.info(
        f"Mode: parallel-all (sequential+dedup) | "
        f"analyses: {len(config['analyses'])} | "
        f"total boundaries: {total}"
    )

    if Path(args.output_dir).exists():
        filter_resume(Path(args.output_dir), program_queues)

    if args.prev_results:
        prev_dirs = [Path(p) for p in args.prev_results]
        if args.report_prededup:
            run_report_prededup(prev_dirs, program_queues)
        else:
            asyncio.run(run_prededup(prev_dirs, program_queues))
    asyncio.run(_run_sequential(program_queues))


# ── Config helpers ────────────────────────────────────────────────────────────


def _load_config(config_path: str) -> dict:
    p = Path(config_path)
    if not p.exists():
        log.error(f"Config file not found: {config_path}")
        sys.exit(1)
    with open(p) as f:
        return json.load(f)


def _find_analysis(args: argparse.Namespace) -> dict:
    """Find one analysis entry in config matching program/tool/algorithm."""
    config = _load_config(args.config)
    for a in config["analyses"]:
        if (
            a["program_name"]   == args.program_name
            and a["sa_tool_name"]   == args.tool_name
            and a["sa_algorithm"]   == args.algorithm
        ):
            return a
    log.error(
        f"No analysis entry found for: "
        f"{args.program_name} / {args.tool_name} / {args.algorithm}"
    )
    sys.exit(1)


# ── Argument parsing ──────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unsoundness Analysis Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # ── single ────────────────────────────────────────────────────────────────
    s = subparsers.add_parser("single", help="One analysis entry, one boundary entry")
    s.add_argument("--program-name",   required=True)
    s.add_argument("--target-program", required=True)
    s.add_argument("--main-class",     required=True, help="e.g. com.example.Main")
    s.add_argument("--sa-tool-name",   required=True, help="e.g. WALA")
    s.add_argument("--sa-algorithm",   required=True, help="e.g. 0-CFA")
    s.add_argument("--sa-tool",        required=True, help="Path to SA tool")
    s.add_argument("--boundaries",     required=True, help="Path to boundaries.json")
    s.add_argument("--boundary-index", type=int, default=0, help="Index into boundaries list (default: 0)")
    s.add_argument("--output-dir",     default="./results")
    s.add_argument("--program_jar_path", required=True, help="path to the jar file")

    # ── parallel-edges ────────────────────────────────────────────────────────
    pe = subparsers.add_parser(
        "parallel-edges",
        help="One analysis entry, all boundaries sequential with oracle dedup",
    )
    pe.add_argument("--config",       required=True, help="Path to pipeline_config.json")
    pe.add_argument("--program-name", required=True)
    pe.add_argument("--tool-name",    required=True, help="e.g. WALA")
    pe.add_argument("--algorithm",    required=True, help="e.g. 0-CFA")
    pe.add_argument("--output-dir",   default="./results")
    pe.add_argument(
        "--prev-results",
        default=None,
        nargs="+",
        metavar="PATH",
        help="One or more previous results directories. All oracle specs found "
             "in each will be run against the new queue to pre-prune equivalent boundaries.",
    )
    pe.add_argument(
        "--report-prededup",
        action="store_true",
        default=False,
        help="When set with --prev-results, skip oracle re-execution and prune the "
             "queue directly from pruning_report.json files in the prev-results directory.",
    )

    # ── parallel-all ──────────────────────────────────────────────────────────
    pa = subparsers.add_parser(
        "parallel-all",
        help="All analyses × all boundaries, sequential with oracle dedup",
    )
    pa.add_argument("--config",     required=True, help="Path to pipeline_config.json")
    pa.add_argument("--output-dir", default="./results")
    pa.add_argument(
        "--prev-results",
        default=None,
        nargs="+",
        metavar="PATH",
        help="One or more previous results directories. All oracle specs found "
             "in each will be run against the new queue to pre-prune equivalent boundaries.",
    )
    pa.add_argument(
        "--report-prededup",
        action="store_true",
        default=False,
        help="When set with --prev-results, skip oracle re-execution and prune the "
             "queue directly from pruning_report.json files in the prev-results directory.",
    )

    return parser.parse_args()


# ── Logging setup ─────────────────────────────────────────────────────────────


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    setup_logging()
    args = parse_args()

    # Build the shared oracle agent once, before any boundary needs it.
    try:
        ensure_agent_built()
    except Exception as e:
        log.error(f"Could not build the oracle agent in oracle_agent/ (needs Maven and a JDK): {e}")
        sys.exit(1)

    if args.mode == "single":
        run_single(args)
    elif args.mode == "parallel-edges":
        run_parallel_edges(args)
    elif args.mode == "parallel-all":
        run_parallel_all(args)


if __name__ == "__main__":
    main()
