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
                 entries run concurrently.

  parallel-all   All analysis entries × all their boundary entries
                 run concurrently.

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

  # parallel-edges — all boundaries for one analysis entry
  python pipeline_orchestrator.py parallel-edges \\
      --config       ./pipeline_config.json \\
      --program-name program_A \\
      --tool-name    WALA \\
      --algorithm    0-CFA \\
      --concurrency  5 \\
      --output-dir   ./results

  # parallel-all — everything in the config, concurrently
  python pipeline_orchestrator.py parallel-all \\
      --config      ./pipeline_config.json \\
      --concurrency 5 \\
      --output-dir  ./results
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.stages import run_pipeline
from pipeline.sampler import run_boundary_sampler

log = logging.getLogger("pipeline.orchestrator")


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
        # Take the last segment of a class descriptor like Lcom/example/Handler
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
) -> PipelineState:
    """
    Build a PipelineState with an isolated output directory.
    Output path: <base>/<program>/<tool>/<algorithm>/<boundary_id>/
    """
    b_id       = _boundary_id(boundary, boundary_index)
    output_dir = base_output_dir / program_name / tool_name / algorithm / b_id
    output_dir.mkdir(parents=True, exist_ok=True)

    return PipelineState(
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
        sys.exit(1)
    log.info(f"Loaded {len(entries)} boundary entries from: {path}")
    return entries


# ── Semaphore-limited runner ──────────────────────────────────────────────────

async def run_with_limit(state: PipelineState, sem: asyncio.Semaphore) -> None:
    """Run one pipeline instance, respecting the concurrency semaphore."""
    async with sem:
        log.info(f"{state.label()} Slot acquired.")
        await run_pipeline(state)


# ── Mode 1: single ────────────────────────────────────────────────────────────

def run_single(args: argparse.Namespace) -> None:
    """One analysis entry, one boundary entry by index."""
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
    )

    log.info(f"Mode: single | {state.label()}")
    asyncio.run(run_pipeline(state))


# ── Mode 2: parallel-edges ────────────────────────────────────────────────────

def run_parallel_edges(args: argparse.Namespace) -> None:
    """One analysis entry from config, all boundary entries concurrently."""
    analysis   = _find_analysis(args)
    boundaries = load_boundaries(analysis["boundaries_file"])
    sem        = asyncio.Semaphore(args.concurrency)

    analysis_output_dir = (
        Path(args.output_dir)
        / analysis["program_name"]
        / analysis["sa_tool_name"]
        / analysis["sa_algorithm"]
    )

    async def run_all():
        # Run sampler to get representative subset
        selected_boundaries, selected_indices = await run_boundary_sampler(
            boundaries=boundaries,
            program_name=analysis["program_name"],
            tool_name=analysis["sa_tool_name"],
            algorithm=analysis["sa_algorithm"],
            output_dir=analysis_output_dir,
            max_boundaries=analysis.get("max_boundaries"),
        )

        # Build states for selected boundaries
        states = [
            make_state(
                program_name=analysis["program_name"],
                tool_name=analysis["sa_tool_name"],
                algorithm=analysis["sa_algorithm"],
                target_program=analysis["target_program"],
                main_class=analysis.get("main_class"),
                sa_tool=analysis["sa_tool"],
                boundary=b,
                boundary_index=orig_i,
                base_output_dir=Path(args.output_dir),
            )
            for b, orig_i in zip(selected_boundaries, selected_indices)
        ]

        log.info(
            f"Mode: parallel-edges | "
            f"{analysis['program_name']}/{analysis['sa_tool_name']}/{analysis['sa_algorithm']} | "
            f"boundaries: {len(states)} (sampled from {len(boundaries)}) | concurrency: {args.concurrency}"
        )

        await asyncio.gather(*[run_with_limit(s, sem) for s in states])

    asyncio.run(run_all())


# ── Mode 3: parallel-all ──────────────────────────────────────────────────────

def run_parallel_all(args: argparse.Namespace) -> None:
    """All analysis entries × all boundary entries, concurrently."""
    config     = _load_config(args.config)
    sem        = asyncio.Semaphore(args.concurrency)

    # Pre-load all boundaries per analysis for the sampler
    analysis_boundaries = [
        (analysis, load_boundaries(analysis["boundaries_file"]))
        for analysis in config["analyses"]
    ]

    async def run_all():
        states = []
        for analysis, boundaries in analysis_boundaries:
            # Run sampler to get representative subset
            selected_boundaries, selected_indices = await run_boundary_sampler(
                boundaries=boundaries,
                program_name=analysis["program_name"],
                tool_name=analysis["sa_tool_name"],
                algorithm=analysis["sa_algorithm"],
                output_dir=(
                    Path(args.output_dir)
                    / analysis["program_name"]
                    / analysis["sa_tool_name"]
                    / analysis["sa_algorithm"]
                ),
                max_boundaries=analysis.get("max_boundaries"),
            )

            # Build states for selected boundaries
            for b, orig_i in zip(selected_boundaries, selected_indices):
                states.append(make_state(
                    program_name=analysis["program_name"],
                    tool_name=analysis["sa_tool_name"],
                    algorithm=analysis["sa_algorithm"],
                    target_program=analysis["target_program"],
                    main_class=analysis.get("main_class"),
                    sa_tool=analysis["sa_tool"],
                    boundary=b,
                    boundary_index=orig_i,
                    base_output_dir=Path(args.output_dir),
                ))

        total_analyses  = len(config["analyses"])
        total_runs      = len(states)
        log.info(
            f"Mode: parallel-all | "
            f"analyses: {total_analyses} | total runs: {total_runs} | "
            f"concurrency: {args.concurrency}"
        )

        await asyncio.gather(*[run_with_limit(s, sem) for s in states])

    asyncio.run(run_all())


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
            a["program_name"]  == args.program_name
            and a["sa_tool_name"]  == args.tool_name
            and a["sa_algorithm"]  == args.algorithm
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

    # ── parallel-edges ────────────────────────────────────────────────────────
    pe = subparsers.add_parser("parallel-edges", help="One analysis entry, all boundaries concurrently")
    pe.add_argument("--config",       required=True, help="Path to pipeline_config.json")
    pe.add_argument("--program-name", required=True)
    pe.add_argument("--tool-name",    required=True, help="e.g. WALA")
    pe.add_argument("--algorithm",    required=True, help="e.g. 0-CFA")
    pe.add_argument("--concurrency",  type=int, default=5)
    pe.add_argument("--output-dir",   default="./results")

    # ── parallel-all ──────────────────────────────────────────────────────────
    pa = subparsers.add_parser("parallel-all", help="All analyses × all boundaries concurrently")
    pa.add_argument("--config",      required=True, help="Path to pipeline_config.json")
    pa.add_argument("--concurrency", type=int, default=5)
    pa.add_argument("--output-dir",  default="./results")

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

    if args.mode == "single":
        run_single(args)
    elif args.mode == "parallel-edges":
        run_parallel_edges(args)
    elif args.mode == "parallel-all":
        run_parallel_all(args)


if __name__ == "__main__":
    main()