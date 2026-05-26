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
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from pipeline.state import PipelineState
from pipeline.stages import run_pipeline
from pipeline.utils import write_final_report
from pipeline.sampler import (
    filter_jvm_runtime_boundaries,
    filter_jdk_caller_boundaries,
)
from config import OUT_PIPELINE_RESULT, OUT_TARGET_ORACLE, OUT_PRUNING_REPORT

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
        sys.exit(1)
    log.info(f"Loaded {len(entries)} boundary entries from: {path}")
    return entries


# ── Oracle deduplication helpers ──────────────────────────────────────────────

def _is_successful(state: PipelineState) -> bool:
    """Read pipeline_result.json and return the success flag."""
    result_path = state.output_dir / OUT_PIPELINE_RESULT
    try:
        return json.loads(result_path.read_text()).get("success", False)
    except Exception:
        return False


def _find_agent_jar(state: PipelineState) -> Optional[Path]:
    """
    Find the built instrumentation agent JAR inside the reproducing test case.
    Looks under <reproducing_test_case_path>/instrumentation/target/*.jar,
    excluding original-*.jar.
    """
    if not state.reproducing_test_case_path:
        return None
    target_dir = Path(state.reproducing_test_case_path) / "instrumentation" / "target"
    jars = [
        j for j in target_dir.glob("*.jar")
        if not j.name.startswith("original-")
    ]
    return jars[0] if jars else None


def _build_classpath(program_jar_path: str) -> Optional[str]:
    """
    Build a colon-separated classpath string from program_jar_path.
    - If it's a file: return it directly.
    - If it's a directory: glob all *.jar files inside.
    Returns None if nothing is found.
    """
    p = Path(program_jar_path)
    if not p.exists():
        return None
    if p.is_file():
        return str(p)
    jars = list(p.glob("*.jar"))
    return ":".join(str(j) for j in jars) if jars else None


async def _run_oracle_on_program(
    agent_jar:        Path,
    program_jar_path: str,
    main_class:       str,
    output_path:      Path,
) -> bool:
    """
    Run the instrumentation agent JAR against a real program to produce an oracle.
    Returns True if output_path was created, False on any failure (fail-open).
    """
    try:
        classpath = _build_classpath(program_jar_path)
        if not classpath:
            log.warning(f"No JARs found at {program_jar_path} — skipping oracle run")
            return False

        cmd = [
            "java",
            f"-javaagent:{agent_jar}={output_path}",
            "-cp", classpath,
            main_class,
        ]
        log.info(f"Running oracle: {' '.join(str(c) for c in cmd)}")
        await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=1800, check=False
        )
        return output_path.exists()
    except Exception as e:
        log.warning(f"Oracle run failed for {program_jar_path}: {e}")
        return False


def _extract_symptoms(oracle_path: Path) -> set[tuple[str, str, str, str]]:
    """
    Parse an oracle JSON file and return a set of
    (caller_class, caller_method, callee_class, callee_method) 4-tuples.
    All values in slash-separated internal format (no L prefix, no semicolon).
    Returns empty set on any parse error.
    """
    try:
        data = json.loads(oracle_path.read_text())
        symptoms: set[tuple[str, str, str, str]] = set()

        for rc_entry in data.values():
            for sym in rc_entry.get("symptoms", []):
                caller_raw = sym.get("caller", "")
                callee_raw = sym.get("callee", "")

                # Caller format: "com.example.App.main([Ljava/lang/String;)V"
                #class : com.example.App
                #method: main
                caller_class = caller_raw.split("(")[0].rsplit(".", 1)[0] if "." in caller_raw else None
                caller_method = caller_raw.split("(")[0].rsplit(".", 1)[1] if "." in caller_raw else None
                

                # Callee format: "java.io.PrintStream.println(Ljava/lang/String;)V"
                callee_class = callee_raw.split("(")[0].rsplit(".", 1)[0] if "." in callee_raw else None
                callee_method = callee_raw.split("(")[0].rsplit(".", 1)[1] if "." in callee_raw else None

                if caller_class and caller_method and callee_class and callee_method:
                    symptoms.add((caller_class, caller_method, callee_class, callee_method))
                else:
                    log.warning(f"Malformed symptom entry in {oracle_path}: {sym}")
                    continue

        return symptoms
    except Exception as e:
        log.warning(f"Failed to extract symptoms from {oracle_path}: {e}")
        return set()


def _prune_queue(
    states:   list[PipelineState],
    symptoms: set[tuple[str, str, str, str]],
) -> tuple[list[PipelineState], list[PipelineState]]:
    """
    Partition states into (kept, pruned) based on symptom matching.
    A state is pruned if its boundary's (caller_class, caller_method,
    callee_class, callee_method) appears in symptoms.
    """
    kept:   list[PipelineState] = []
    pruned: list[PipelineState] = []

    for state in states:
        b = state.boundary
        caller_class  = b.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";")
        caller_method = b.get("caller", {}).get("name", "")
        callee_class  = b.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";")
        callee_method = b.get("missedCallee", {}).get("name", "")

        # replase / with . in classnames to match symptom format
        caller_class = caller_class.replace("/", ".")
        callee_class = callee_class.replace("/", ".")

        if (caller_class, caller_method, callee_class, callee_method) in symptoms:
            pruned.append(state)
        else:
            kept.append(state)

    return kept, pruned


def _write_pruning_report(
    state:       PipelineState,
    pruned:      list[PipelineState],
    oracle_runs: list[dict],
) -> None:
    """Write pruning_report.json to the successful boundary's output directory."""
    try:
        report = {
            "timestamp": datetime.now().isoformat(),
            "triggered_by": {
                "boundary_id":   state.boundary_id,
                "program_name":  state.program_name,
                "tool_name":     state.tool_name,
                "algorithm":     state.algorithm,
            },
            "oracle_runs":   oracle_runs,
            "pruned_count":  len(pruned),
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


async def _run_dedup(
    successful_state: PipelineState,
    program_queues:   dict[str, tuple[dict, list[PipelineState]]],
) -> None:
    """
    After a successful pipeline run, run the oracle agent against every program
    that still has remaining boundaries in the queue and prune matching entries.
    Fail-open: failures in any step are logged and skipped.
    """
    agent_jar = _find_agent_jar(successful_state)
    if not agent_jar:
        log.warning(f"{successful_state.label()} Agent JAR not found — skipping dedup")
        return

    all_pruned:  list[PipelineState] = []
    oracle_runs: list[dict]          = []

    for program_key, (analysis, remaining) in program_queues.items():
        if not remaining:
            continue

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

        ok = await _run_oracle_on_program(agent_jar, jar_path, main_class, oracle_out)
        oracle_runs.append({
            "program_name": analysis["program_name"],
            "oracle_path":  str(oracle_out),
            "status":       "ok" if ok else "failed",
        })

        if ok:
            symptoms = _extract_symptoms(oracle_out)
            if symptoms:
                kept, pruned = _prune_queue(remaining, symptoms)
                program_queues[program_key] = (analysis, kept)
                all_pruned.extend(pruned)
                if pruned:
                    log.info(
                        f"{successful_state.label()} Pruned {len(pruned)} boundaries "
                        f"from {analysis['program_name']}: "
                        f"{[s.boundary_id for s in pruned]}"
                    )

    if all_pruned:
        _write_pruning_report(successful_state, all_pruned, oracle_runs)


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

        if _is_successful(state):
            await _run_dedup(state, program_queues)


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

    # ── parallel-all ──────────────────────────────────────────────────────────
    pa = subparsers.add_parser(
        "parallel-all",
        help="All analyses × all boundaries, sequential with oracle dedup",
    )
    pa.add_argument("--config",     required=True, help="Path to pipeline_config.json")
    pa.add_argument("--output-dir", default="./results")

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
