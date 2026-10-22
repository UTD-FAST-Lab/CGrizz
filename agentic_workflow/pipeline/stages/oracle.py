"""
pipeline/stages/oracle.py
==========================
Stage 5 (oracle spec for the test case) and stage 5b (the same spec on the
real program).

The instrumentation agent writes a declarative oracle_spec.json; the prebuilt
oracle agent in oracle_agent/ applies it. The pipeline — not the LLM — runs the
oracle and validates the result, so every failure comes back to the agent (or
to the runner) as a concrete, classified problem.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.agents import build_agent_options
from pipeline.agent_runner import run_agent
from pipeline import oracle
from pipeline.oracle import OracleRun, Verdict, INFRA
from config import (
    OUT_ORACLE,
    OUT_TARGET_ORACLE,
    ORACLE_AGENT_DIR,
    ORACLE_GENERATE_ATTEMPTS,
)

log = logging.getLogger("pipeline")

SPEC_REFERENCE = ORACLE_AGENT_DIR / "SPEC.md"


# ── Stage 5: Oracle spec on the test case ─────────────────────────────────────


@timed("oracle_generation")
async def stage_generate_oracle(state: PipelineState, feedback: str = "") -> bool:
    """
    Ask the instrumentation agent for an oracle spec, then run it on the
    reproducing test case and validate the oracle.

    feedback == ""  → fresh run (first run, or after the report was revised):
                      stale instrumentation from an earlier loop is removed.
    feedback != ""  → refinement after a failed check on the real program:
                      the agent edits the existing spec.

    On failure state.oracle_failure / state.oracle_failure_kind say why.
    """
    repro = Path(state.reproducing_test_case_path)
    spec = oracle.spec_path(repro)
    oracle_path = repro / OUT_ORACLE
    state.oracle_failure = state.oracle_failure_kind = None

    if not feedback and state.loop_count > 0 and spec.parent.exists():
        shutil.rmtree(spec.parent)
        log.info(f"{state.label()} Deleted stale instrumentation dir: {spec.parent}")
    if feedback and spec.exists():
        _archive_spec(state, spec)
    spec.parent.mkdir(parents=True, exist_ok=True)

    problem, kind = feedback, ""
    for attempt in range(1, ORACLE_GENERATE_ATTEMPTS + 1):
        await run_agent(
            prompt=_oracle_prompt(state, spec, problem),
            options=build_agent_options(state),
            stage_name=f"{state.label()} OracleGeneration",
        )
        problem, kind = await _validate_on_test_case(state, spec, oracle_path)
        if not problem:
            state.oracle_spec_path = str(spec)
            state.oracle_path = str(oracle_path)
            state.instrumentation = str(spec.parent)
            log.info(f"{state.label()} Oracle spec validated on the test case → {spec}")
            return True
        log.warning(f"{state.label()} Oracle spec attempt {attempt}/{ORACLE_GENERATE_ATTEMPTS} rejected: {problem}")
        if kind == "unsupported":
            break

    state.oracle_failure = problem
    state.oracle_failure_kind = "oracle_unsupported" if kind == "unsupported" else "instrumentation_failure"
    log.error(f"{state.label()} Oracle generation failed: {problem}")
    return False


def _oracle_prompt(state: PipelineState, spec: Path, problem: str) -> str:
    edge = state.boundary_summary().splitlines()[:2]
    fix = (
        f"\n## Problem with the current spec — fix it\n\n{problem}\n\n"
        f"Edit {spec} in place (do not start from scratch unless it is unusable).\n"
        if problem else ""
    )
    return f"""\
Use the instrumentation agent to write the oracle spec for this boundary.
Invoke it via the Task tool.

## Inputs

Unsoundness report     : {state.unsoundness_report}
Reproducing test case  : {state.reproducing_test_case_path}/
Spec to write          : {spec}
Spec reference         : {SPEC_REFERENCE}
Oracle agent jar       : {oracle.ORACLE_AGENT_JAR}
Real-program boundary  : {' | '.join(edge)}
{fix}
Return JSON only:
{{
  "spec_path": "{spec}",
  "success": true | false
}}"""


async def _validate_on_test_case(state: PipelineState, spec: Path, oracle_path: Path) -> tuple[str, str]:
    """('', '') if the spec is valid and yields symptoms on the test case, else (problem, kind)."""
    problems = oracle.lint_spec(spec, Path(state.reproducing_test_case_path))
    if problems:
        return "The spec was rejected before running:\n- " + "\n- ".join(problems), "lint"
    run = await run_on_test_case(state, oracle_path)
    if run is None:
        return ("The reproducing test case could not be built or has no jar / main class "
                "(target/*.jar and jcg.conf 'main').", "test_case")
    if run.status == "unsupported":
        return f"The spec is marked unsupported: {run.stats.get('unsupported')}", "unsupported"
    if run.status == "spec_invalid":
        return "The oracle agent rejected the spec:\n- " + "\n- ".join(run.stats.get("specErrors", [])), "spec"
    if run.status != "ok":
        return f"The oracle run on the test case failed ({run.status}). Output:\n{run.output_tail[-1500:]}", "run"
    if not oracle.symptom_edges(run.oracle):
        return ("The spec produced no symptoms on the test case. Statistics: "
                f"{json.dumps({k: run.stats.get(k) for k in ('rootCauseSites', 'rootCauseHits', 'symptomSites', 'taggedObjects')})}. "
                "rootCauseSites = 0 → no instruction matched; rootCauseHits = 0 → matched but never executed or "
                "produced null; hits > 0 → the tagged value never reached a call site (check tag / propagate / "
                "symptoms.match).", "empty")
    return "", ""


def _archive_spec(state: PipelineState, spec: Path) -> None:
    n = len(list(spec.parent.glob("oracle_spec.attempt*.json")))
    shutil.copy(spec, spec.with_name(f"oracle_spec.attempt{n}.json"))


def _test_case_jar_and_main(repro: Path) -> tuple[Optional[Path], Optional[str]]:
    try:
        main = json.loads((repro / "jcg.conf").read_text()).get("main")
    except Exception:
        main = None
    jars = sorted(p for p in (repro / "target").glob("*.jar") if not p.name.startswith("original-"))
    return (jars[0] if jars else None), main


async def run_on_test_case(state: PipelineState, oracle_path: Path) -> Optional[OracleRun]:
    """Build the reproducing test case if needed and run the oracle on its jar."""
    repro = Path(state.reproducing_test_case_path)
    jar, main = _test_case_jar_and_main(repro)
    if jar is None and (repro / "pom.xml").exists():
        subprocess.run(["mvn", "-q", "-B", "package", "-DskipTests"], cwd=repro, check=False)
        jar, main = _test_case_jar_and_main(repro)
    if jar is None or not main:
        return None
    return await oracle.run_oracle(oracle.spec_path(repro), str(jar), main, oracle_path)


async def generate_oracle_testcase(state: PipelineState) -> bool:
    """Regenerate oracle.json in the reproducing test case (after the reproduction rebuild)."""
    run = await run_on_test_case(state, Path(state.reproducing_test_case_path) / OUT_ORACLE)
    if run is None or run.status != "ok":
        log.warning(f"{state.label()} Oracle run against test case failed — oracle.json may not be generated.")
        return False
    log.info(f"{state.label()} Oracle.json generated in reproducing test case directory.")
    return True


# ── Stage 5b: Oracle check on the real program ────────────────────────────────


@timed("oracle_check")
async def stage_check_oracle_on_program(state: PipelineState) -> Verdict:
    """
    Run the spec against the real target program and classify the result for
    this boundary (see pipeline.oracle.classify). The verdict is recorded in
    state.oracle_verdicts.
    """
    if not state.program_jar_path:
        verdict = Verdict("run_failed", INFRA, "No program_jar_path configured for this program.")
    else:
        run = await oracle.run_oracle_on_jar(
            Path(state.oracle_spec_path), state.program_jar_path, state.main_class,
            state.output_dir / OUT_TARGET_ORACLE,
        )
        state.target_oracle_path = str(run.oracle_path)
        state.oracle_run_log_path = str(run.log_path) if run.log_path else None
        state.oracle_run_output = run.output_tail
        verdict = oracle.classify(run, state.boundary)
        state.oracle_verdicts.append({**verdict.as_dict(), "loop": state.loop_count, "run": run.summary()})
    log.info(f"{state.label()} Oracle on real program: {verdict.kind} ({verdict.category})")
    if verdict.kind != "found":
        log.info(f"{state.label()} {verdict.message}")
    return verdict


def build_oracle_feedback_section(state: PipelineState, verdict: Verdict) -> str:
    """Feedback for the instrumentation agent after a failed check on the real program."""
    lines = [
        f"The spec was run on the REAL program ({state.program_name}) and the boundary edge was not a symptom.",
        f"Verdict: {verdict.kind}",
        verdict.message,
    ]
    if state.oracle_run_log_path and Path(state.oracle_run_log_path).exists():
        lines.append(f"Full JVM output of that run: {state.oracle_run_log_path}")
    if state.target_oracle_path and Path(state.target_oracle_path).exists():
        lines.append(f"Oracle produced on the real program: {state.target_oracle_path}")
    lines.append(
        "Generalize the spec so it captures this pattern in the real program too; it must still "
        "produce symptoms on the test case. Do not run anything against the real program yourself."
    )
    return "\n".join(lines)
