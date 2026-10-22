"""
pipeline/oracle.py
==================
The spec-driven oracle.

The instrumentation agent no longer writes ASM code. It writes a declarative
`oracle_spec.json` (see oracle_agent/SPEC.md); the prebuilt, tested Java agent
in oracle_agent/ applies it. This module:

  1. builds the shared agent jar once,
  2. runs it against a test case or a real program,
  3. lints a spec for test-case-specific matchers, and
  4. classifies an oracle run for a given boundary into a verdict, so the
     pipeline can tell instrumentation problems apart from a wrong hypothesis.

No claude_agent_sdk import here — everything is plain subprocess + JSON.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import (
    ORACLE_AGENT_DIR,
    ORACLE_AGENT_JAR,
    ORACLE_SPEC_FILE,
    ORACLE_RUN_TIMEOUT,
    OUT_TEST_CASE_DIR,
)

log = logging.getLogger("pipeline")

# Verdict categories (see classify()).
FOUND    = "found"
PATTERN  = "pattern"          # the oracle ran fine; the hypothesis does not explain the edge
SPEC     = "spec"             # the spec itself is invalid — fixable by the instrumentation agent
INFRA    = "infrastructure"   # the run itself failed (timeout, JVM/agent failure)
UNSUPPORTED = "unsupported"   # the spec language cannot express the pattern


# ── Building the agent ────────────────────────────────────────────────────────

_build_lock = threading.Lock()


def _sources_newer_than_jar() -> bool:
    if not ORACLE_AGENT_JAR.exists():
        return True
    jar_mtime = ORACLE_AGENT_JAR.stat().st_mtime
    sources = [ORACLE_AGENT_DIR / "pom.xml", *(ORACLE_AGENT_DIR / "src").rglob("*.java")]
    return any(p.stat().st_mtime > jar_mtime for p in sources)


def ensure_agent_built() -> Path:
    """Build oracle_agent/ with Maven if the jar is missing or stale. Thread-safe."""
    with _build_lock:
        if _sources_newer_than_jar():
            log.info(f"Building oracle agent in {ORACLE_AGENT_DIR} ...")
            subprocess.run(["mvn", "-q", "-B", "package"], cwd=ORACLE_AGENT_DIR, check=True)
            log.info(f"Oracle agent built → {ORACLE_AGENT_JAR}")
    return ORACLE_AGENT_JAR


# ── Locating specs ────────────────────────────────────────────────────────────


def spec_path(reproducing_test_case: str | Path) -> Path:
    return Path(reproducing_test_case) / "instrumentation" / ORACLE_SPEC_FILE


def find_spec_from_dir(boundary_dir: Path) -> Optional[Path]:
    """The spec of a previous boundary run (for dedup), if any."""
    p = spec_path(boundary_dir / OUT_TEST_CASE_DIR / "reproducing")
    return p if p.exists() else None


def stats_path(oracle_path: Path) -> Path:
    """Mirror of Tracker.statsPath in the Java agent."""
    s = str(oracle_path)
    return Path(s[:-5] + ".stats.json") if s.endswith(".json") else Path(s + ".stats.json")


# ── Running ───────────────────────────────────────────────────────────────────


@dataclass
class OracleRun:
    status:      str                 # ok | timeout | no_output | spec_invalid | unsupported | launch_error
    oracle_path: Path
    exit_code:   Optional[int] = None
    stats:       dict = field(default_factory=dict)
    log_path:    Optional[Path] = None
    output_tail: str = ""

    @property
    def oracle(self) -> dict:
        try:
            return json.loads(self.oracle_path.read_text())
        except Exception:
            return {}

    def summary(self) -> dict:
        """Compact, JSON-serialisable record for pipeline_result.json / pruning reports."""
        keys = ("rootCauseSites", "rootCauseHits", "symptomsRecorded", "classesInstrumented",
                "instrumentationErrors", "specErrors", "unsupported")
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "oracle_path": str(self.oracle_path),
            "log_path": str(self.log_path) if self.log_path else None,
            **{k: self.stats.get(k) for k in keys if k in self.stats},
        }


def build_classpath(program_jar_path: str) -> Optional[str]:
    """A jar file, or every *.jar in a directory (sorted, so runs are reproducible)."""
    p = Path(program_jar_path).resolve()
    if not p.exists():
        return None
    if p.is_file():
        return str(p)
    jars = sorted(p.glob("*.jar"))
    return ":".join(str(j) for j in jars) if jars else None


def _run_blocking(cmd: list[str], cwd: str, timeout: int) -> tuple[Optional[int], bool, str]:
    """Run the JVM; on timeout send SIGTERM first so shutdown hooks write the oracle."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            out, _ = proc.communicate(timeout=60)
        except Exception:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
    return proc.returncode, timed_out, (out or b"").decode("utf-8", errors="replace")


async def run_oracle(
    spec: Path,
    classpath: str,
    main_class: str,
    oracle_path: Path,
    timeout: int = ORACLE_RUN_TIMEOUT,
) -> OracleRun:
    """Run `main_class` on `classpath` under the oracle agent with `spec`."""
    oracle_path = Path(oracle_path).resolve()
    oracle_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (oracle_path, stats_path(oracle_path)):
        stale.unlink(missing_ok=True)
    log_path = oracle_path.with_suffix(".log")

    try:
        jar = await asyncio.to_thread(ensure_agent_built)
    except Exception as e:
        return OracleRun("launch_error", oracle_path, output_tail=f"could not build oracle agent: {e}")

    cmd = [
        "java",
        "-XX:-OmitStackTraceInFastThrow",   # implicit exceptions must keep their stack traces
        f"-javaagent:{jar}={Path(spec).resolve()}",
        f"-Dcgrizz.oracle.out={oracle_path}",
        "-cp", classpath,
        main_class,
    ]
    log.info(f"Running oracle: {' '.join(cmd)}")
    # Run from a scratch dir so programs that write files do not pollute results.
    work = oracle_path.parent / f".{oracle_path.stem}_cwd"
    work.mkdir(exist_ok=True)
    try:
        code, timed_out, output = await asyncio.to_thread(_run_blocking, cmd, str(work), timeout)
    except Exception as e:
        return OracleRun("launch_error", oracle_path, output_tail=str(e))

    try:
        log_path.write_text(output)
    except Exception:
        log_path = None

    stats = {}
    try:
        stats = json.loads(stats_path(oracle_path).read_text())
    except Exception:
        pass

    if not stats:
        status = "timeout" if timed_out else "no_output"
    elif stats.get("unsupported"):
        status = "unsupported"
    elif stats.get("specErrors"):
        status = "spec_invalid"
    elif timed_out:
        status = "timeout"
    else:
        status = "ok"
    return OracleRun(status, oracle_path, code, stats, log_path, output[-5000:])


async def run_oracle_on_jar(spec: Path, program_jar_path: str, main_class: str, oracle_path: Path) -> OracleRun:
    classpath = build_classpath(program_jar_path)
    if not classpath:
        return OracleRun("launch_error", Path(oracle_path), output_tail=f"No JARs found at {program_jar_path}")
    return await run_oracle(spec, classpath, main_class, Path(oracle_path))


# ── Symptoms ──────────────────────────────────────────────────────────────────


def _split_method(m: str) -> tuple[Optional[str], Optional[str]]:
    head = m.split("(")[0]
    if "." not in head:
        return None, None
    cls, name = head.rsplit(".", 1)
    return cls, name


def symptom_edges(oracle: dict) -> set[tuple[str, str, str, str]]:
    """(caller_class, caller_method, callee_class, callee_method) for every symptom."""
    edges = set()
    for rc in oracle.values():
        for sym in rc.get("symptoms", []):
            cc, cm = _split_method(sym.get("caller", ""))
            ec, em = _split_method(sym.get("callee", ""))
            if cc and cm and ec and em:
                edges.add((cc, cm, ec, em))
    return edges


def extract_symptoms(oracle_path: Path) -> set[tuple[str, str, str, str]]:
    """symptom_edges() of an oracle.json file; empty set if it cannot be read."""
    try:
        return symptom_edges(json.loads(Path(oracle_path).read_text()))
    except Exception as e:
        log.warning(f"Failed to extract symptoms from {oracle_path}: {e}")
        return set()


def boundary_edge(boundary: dict) -> tuple[str, str, str, str]:
    def cls(d: dict) -> str:
        return d.get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
    caller, callee = boundary.get("caller", {}), boundary.get("missedCallee", {})
    return cls(caller), caller.get("name", ""), cls(callee), callee.get("name", "")


# ── Spec lint ─────────────────────────────────────────────────────────────────


def _test_case_packages(test_case_dir: Path) -> set[str]:
    pkgs = set()
    src = test_case_dir / "src" / "main" / "java"
    for f in src.rglob("*.java") if src.exists() else []:
        rel = f.parent.relative_to(src)
        if rel.parts:
            pkgs.add(".".join(rel.parts))
    return pkgs


def lint_spec(spec: Path, test_case_dir: Optional[Path] = None) -> list[str]:
    """
    Cheap structural checks done before any JVM run (full validation happens in
    the Java agent). The important one: matchers must describe the pattern, not
    the test case — a spec that names the test case's own classes cannot fire on
    the real program and makes grouping meaningless.
    """
    try:
        data = json.loads(Path(spec).read_text())
    except FileNotFoundError:
        return [f"{spec} does not exist"]
    except Exception as e:
        return [f"{spec} is not valid JSON: {e}"]
    if not isinstance(data, dict):
        return ["the spec must be a JSON object"]
    if data.get("unsupported"):
        return []
    problems = []
    rcs = data.get("rootCauses")
    if not isinstance(rcs, list) or not rcs:
        problems.append("'rootCauses' must be a non-empty array")
        rcs = []
    if test_case_dir:
        pkgs = _test_case_packages(Path(test_case_dir))
        for rc in rcs:
            if not isinstance(rc, dict):
                continue
            globs = [rc.get("owner"), (rc.get("in") or {}).get("owner")]
            for g in filter(None, globs):
                literal = str(g).split("*")[0].rstrip(".$")
                for pkg in pkgs:
                    if literal and (literal == pkg or literal.startswith(pkg + ".")):
                        problems.append(
                            f"root cause '{rc.get('id')}' matches '{g}', a class of the test case "
                            f"(package {pkg}). Match the API or instruction pattern instead "
                            f"(e.g. java.lang.Class.forName, sun.misc.Unsafe.get*Object*, CHECKCAST), "
                            f"so the same spec also fires on the real program."
                        )
    return problems


# ── Verdict ───────────────────────────────────────────────────────────────────


@dataclass
class Verdict:
    kind:     str     # found | rc_not_matched | rc_not_hit | no_symptoms | edge_missing | spec_invalid | unsupported | timeout | run_failed
    category: str     # FOUND | PATTERN | SPEC | INFRA | UNSUPPORTED
    message:  str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "category": self.category, "message": self.message}


def classify(run: OracleRun, boundary: dict) -> Verdict:
    """Decide what an oracle run on the real program says about this boundary."""
    caller_cls, caller_m, callee_cls, callee_m = edge = boundary_edge(boundary)
    edge_txt = f"{caller_cls}.{caller_m} → {callee_cls}.{callee_m}"
    stats = run.stats

    if run.status == "unsupported":
        return Verdict("unsupported", UNSUPPORTED, f"The spec is marked unsupported: {stats.get('unsupported')}")
    if run.status == "spec_invalid":
        return Verdict("spec_invalid", SPEC, "The oracle agent rejected the spec:\n- " + "\n- ".join(stats.get("specErrors", [])))

    edges = symptom_edges(run.oracle)
    if edge in edges:
        return Verdict("found", FOUND, f"Boundary edge {edge_txt} is a symptom.")

    if run.status in ("no_output", "launch_error"):
        return Verdict("run_failed", INFRA,
                       f"The oracle run produced no output (exit code {run.exit_code}). "
                       f"The JVM or the program failed to start. Last output:\n{run.output_tail[-1500:]}")
    if run.status == "timeout" and not edges:
        return Verdict("timeout", INFRA, f"The oracle run timed out after {ORACLE_RUN_TIMEOUT}s without symptoms.")

    sites = stats.get("rootCauseSites", {})
    hits = stats.get("rootCauseHits", {})
    if not any(sites.values()):
        return Verdict("rc_not_matched", PATTERN,
                       "No instruction in the program's (non-JDK) classes matched any root cause of the spec "
                       f"(sites per root cause: {sites}). Either the matcher is too narrow (owner/name/desc), "
                       "or the root cause happens only inside JDK code — then match the application-side call "
                       "that reaches it (e.g. the JDK API the program calls).")
    if not any(hits.values()):
        return Verdict("rc_not_hit", PATTERN,
                       f"Root-cause instructions exist ({sites}) but none executed and produced a value on this run. "
                       "The program's execution does not go through this pattern.")
    if not edges:
        return Verdict("no_symptoms", PATTERN,
                       f"Root causes fired ({hits}) but no tagged value reached a call site. "
                       "Check the 'tag' choice (return/receiver/argN/[] elements), add 'propagate' rules for the "
                       "steps in between, or the symptom matching mode (receiver / receiverClass / callerClass).")
    sample = sorted(f"{a}.{b} → {c}.{d}" for a, b, c, d in edges)[:15]
    near = sorted(f"{a}.{b} → {c}.{d}" for a, b, c, d in edges if (a, b) == (caller_cls, caller_m))[:10]
    msg = (f"Root causes fired and produced {len(edges)} symptom edges, but not the boundary edge {edge_txt}.\n"
           f"Symptoms at the same caller: {near or 'none'}\nSample of symptoms: {sample}")
    return Verdict("edge_missing", PATTERN, msg)
