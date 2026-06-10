"""
pipeline/utils.py
=================
Shared helpers used across stages.
No pipeline logic lives here — only pure utilities.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from pipeline.state import PipelineState
from config import (
    OUT_PIPELINE_RESULT,
    OUT_PRUNING_REPORT,
    MAX_AGENT_RETRIES,
    AGENT_RETRY_BASE_DELAY,
    MAX_CONCURRENT_AGENTS,
)

# ── Agent concurrency limiter ─────────────────────────────────────────────────

# Lazily initialised inside the event loop — caps concurrent SDK sessions
# independently of the pipeline-level semaphore.
_agent_sem: Optional[asyncio.Semaphore] = None


def _get_agent_sem() -> asyncio.Semaphore:
    global _agent_sem
    if _agent_sem is None:
        _agent_sem = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_sem


# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline")


# ── Agent runner ──────────────────────────────────────────────────────────────

async def run_agent(prompt: str, options: ClaudeAgentOptions, stage_name: str) -> str:
    """
    Run a single agent query and collect the full text response.

    Retries up to MAX_AGENT_RETRIES times on transient SDK initialization
    timeouts (e.g. "Control request timeout: initialize") that occur under
    high concurrency, using exponential backoff.

    The SDK yields three message types:
      - SystemMessage  : internal SDK housekeeping — no content, skip it
      - AssistantMessage: Claude's streamed response blocks — collect text blocks
      - ResultMessage  : final result with metadata — prefer result.result if
                         present, otherwise already captured from AssistantMessage
    """
    log.info(f"[{stage_name}] Starting...")

    for attempt in range(MAX_AGENT_RETRIES):
        collected: list[str] = []
        final_result: Optional[str] = None

        try:
            async with _get_agent_sem():
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, SystemMessage):
                        continue
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "type") and block.type == "text":
                                collected.append(block.text)
                    elif isinstance(message, ResultMessage):
                        if message.result:
                            final_result = message.result
                        log.debug(
                            f"[{stage_name}] Result metadata — "
                            f"stop_reason={message.stop_reason}, "
                            f"turns={message.num_turns}"
                        )

            result = final_result or "\n".join(collected)
            log.info(f"[{stage_name}] Done. Response: {len(result)} chars")
            return result

        except Exception as e:
            err_str = str(e)
            is_retriable = (
                "Control request timeout" in err_str
                or "TimeoutError" in type(e).__name__
                or "Command failed" in err_str
                or "exit code" in err_str
                or "error result" in err_str   # SDK wraps CLI non-zero exit as "error result: <subtype>"
            )
            if is_retriable and attempt < MAX_AGENT_RETRIES - 1:
                delay = AGENT_RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    f"[{stage_name}] Retriable agent error (attempt {attempt + 1}/{MAX_AGENT_RETRIES}), "
                    f"retrying in {delay}s: {err_str}"
                )
                await asyncio.sleep(delay)
            else:
                raise

    return ""  # unreachable, satisfies type checker


# ── JSON extraction ───────────────────────────────────────────────────────────

def parse_json_from_response(response: str) -> Optional[dict]:
    """
    Extract a JSON object from an agent response.
    Tries direct parse first, then searches for the first {...} block.
    Returns None if no valid JSON is found — callers must handle this.
    """
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    start = response.find("{")
    end   = response.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

    log.warning("Could not parse JSON from agent response.")
    return None


# ── Final report ──────────────────────────────────────────────────────────────

def write_final_report(state: PipelineState, success: bool, reason: str) -> None:
    """
    Write pipeline_result.json to the output directory.
    Always called at the end of the pipeline, whether success or failure.
    """
    report = {
        "timestamp":  datetime.now().isoformat(),
        "success":    success,
        "loops_used": state.loop_count,
        "reason":     reason,
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
        },
        "feedback_history": {
            "reproduce_feedback": state.reproduce_feedback,
            "trace_feedback":     state.trace_feedback,
        },
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


# ── Oracle helpers ────────────────────────────────────────────────────────────

def find_agent_jar(state: PipelineState) -> Optional[Path]:
    """Find the built instrumentation agent JAR inside the reproducing test case."""
    if not state.reproducing_test_case_path:
        return None
    target_dir = Path(state.reproducing_test_case_path) / "instrumentation" / "target"
    jars = [j for j in target_dir.glob("*.jar") if not j.name.startswith("original-")]
    return jars[0] if jars else None


def find_agent_jar_from_dir(boundary_dir: Path) -> Optional[Path]:
    """Find the built instrumentation agent JAR given a previous boundary output directory."""
    target_dir = boundary_dir / "test_case" / "reproducing" / "instrumentation" / "target"
    if not target_dir.exists():
        return None
    jars = [j for j in target_dir.glob("*.jar") if not j.name.startswith("original-")]
    return jars[0] if jars else None


def build_classpath(program_jar_path: str) -> Optional[str]:
    """
    Build a colon-separated classpath string from program_jar_path.
    If it's a file return it directly; if a directory glob all *.jar files inside.
    Returns None if nothing is found.
    """
    p = Path(program_jar_path).resolve()
    if not p.exists():
        return None
    if p.is_file():
        return str(p)
    jars = list(p.glob("*.jar"))
    return ":".join(str(j) for j in jars) if jars else None


async def run_oracle_on_program(
    agent_jar: Path,
    program_jar_path: str,
    main_class: str,
    output_path: Path,
) -> tuple[bool, bool, str, "Path | None"]:
    """
    Run the instrumentation agent JAR against a real program to produce an oracle.
    Returns (exists, crashed, run_output, log_path) where:
      - exists:     True if output_path was written
      - crashed:    True if the JVM exited with a non-zero return code (instrumentation error)
      - run_output: combined stdout+stderr truncated to 5000 chars
      - log_path:   Path to oracle_run.log (None if write failed)
    The full output is also written to oracle_run.log next to output_path.
    """
    try:
        agent_jar = agent_jar.resolve()
        output_path = output_path.resolve()
        classpath = build_classpath(program_jar_path)
        if not classpath:
            log.warning(f"No JARs found at {program_jar_path} — skipping oracle run")
            return False, True, "No JARs found at the program_jar_path.", None

        cmd = [
            "java",
            f"-javaagent:{agent_jar}={output_path}",
            "-cp", classpath,
            main_class,
        ]
        log.info(f"Running oracle: {' '.join(str(c) for c in cmd)}")
        tmp_dir = tempfile.mkdtemp(prefix="oracle_run_")
        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, stdin=subprocess.DEVNULL,
                timeout=500, check=False, cwd=tmp_dir,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        combined = (stdout + stderr).strip()

        log_path = output_path.parent / "oracle_run.log"
        wrote_log = False
        try:
            log_path.write_text(combined)
            wrote_log = True
        except Exception:
            pass

        crashed = result.returncode != 0
        run_output = combined[-5000:] if len(combined) > 5000 else combined
        return output_path.exists(), crashed, run_output, (log_path if wrote_log else None)
    except Exception as e:
        log.warning(f"Oracle run failed for {program_jar_path}: {e}")
        return False, True, str(e), None


def extract_symptoms(oracle_path: Path) -> set[tuple[str, str, str, str]]:
    """
    Parse an oracle JSON file and return a set of
    (caller_class, caller_method, callee_class, callee_method) 4-tuples
    in dotted class-name format.
    Returns empty set on any parse error.
    """
    try:
        data = json.loads(oracle_path.read_text())
        symptoms: set[tuple[str, str, str, str]] = set()

        for rc_entry in data.values():
            for sym in rc_entry.get("symptoms", []):
                caller_raw = sym.get("caller", "")
                callee_raw = sym.get("callee", "")

                caller_class  = caller_raw.split("(")[0].rsplit(".", 1)[0] if "." in caller_raw else None
                caller_method = caller_raw.split("(")[0].rsplit(".", 1)[1] if "." in caller_raw else None
                callee_class  = callee_raw.split("(")[0].rsplit(".", 1)[0] if "." in callee_raw else None
                callee_method = callee_raw.split("(")[0].rsplit(".", 1)[1] if "." in callee_raw else None

                if caller_class and caller_method and callee_class and callee_method:
                    symptoms.add((caller_class, caller_method, callee_class, callee_method))
                else:
                    log.warning(f"Malformed symptom entry in {oracle_path}: {sym}")

        return symptoms
    except Exception as e:
        log.warning(f"Failed to extract symptoms from {oracle_path}: {e}")
        return set()


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