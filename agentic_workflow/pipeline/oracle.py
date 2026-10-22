"""
pipeline/oracle.py
==================
Finding and running the instrumentation agent JAR built for a boundary, and
reading the symptoms out of an oracle.json.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from pipeline.state import PipelineState

log = logging.getLogger("pipeline")


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
                timeout=3600, check=False, cwd=tmp_dir,
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
