#!/usr/bin/env python3
"""
failed_regrouping.py
====================
Post-hoc question: **could the failed boundaries have been grouped by the
successful ones?**

The orchestrator's oracle dedup (`_run_dedup`) only prunes boundaries that are
still in the *queue*. A boundary that was attempted and failed has already been
popped, so every success that ran *after* it never got the chance to group it.
This script closes that gap without re-running the pipeline.

Two phases:

  1. **Dry run** (default, no JVM):  for every failed boundary, replay every
     oracle JSON already on disk that was produced by a *successful* boundary of
     the same tool and that targets the failed boundary's program
     (`oracle_target.json`, `oracles/`, `oracles_prededup/`, plus anything this
     script itself produced earlier). If the failed boundary's caller->callee
     edge shows up as a symptom, it is regroupable.

  2. **--run**:  for the (success, program) pairs that have no oracle on disk,
     run the successful boundary's instrumentation agent JAR against the failed
     boundary's program and check the fresh oracle. Work is batched per program
     (one JVM run covers every failed boundary of that program at once) and
     short-circuits as soon as all of a program's failed boundaries are
     explained.

Matching mirrors `pipeline/utils.py::extract_symptoms` + `prune_queue`:
the primary key is (caller_class, caller_method, callee_class, callee_method)
with no descriptor. A stricter descriptor-aware match is computed alongside and
reported separately, since name-only matching can be fooled by overloads.

Nothing here touches the existing `pruning_report.json` files, so `RQ1_table.py`
keeps reporting the original numbers. Results land in
`<results-root>/failed_regrouping/`.

Usage
-----
    # free pass: existing oracles only, no JVM runs
    python failed_regrouping.py --dry-run

    # then spend the runs (per tool, with a budget)
    python failed_regrouping.py --run --tool wala --max-runs 100 --jobs 4
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Layout ────────────────────────────────────────────────────────────────────

OUTPUT_PATH  = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')
REPO_ROOT    = Path(__file__).resolve().parents[2]
AGENTIC_DIR  = REPO_ROOT / 'agentic_workflow'   # config paths are relative to this
CONFIG_DIR   = REPO_ROOT / 'ignored_stuff' / 'configs'
INPUT_DATA   = REPO_ROOT / 'input_data'
PAPER_DIR    = REPO_ROOT / 'scripts' / 'paper'

TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

PIPELINE_FILE = 'pipeline_result.json'
REPORT_FILE   = 'unsoundness_report.json'
TARGET_ORACLE = 'oracle_target.json'

# Directories inside a successful boundary dir that hold oracle_<program>.json.
ORACLE_DIRS = {
    'oracles':          'dedup',
    'oracles_prededup': 'prededup',
}

# This script never writes inside the results tree. Fresh oracles go to
# <results-root>/failed_regrouping/oracles/<program>__<boundary_id>/, so the
# boundary dirs, their pruning_report.json, and RQ1_table.py stay untouched.
OUT_DIR_NAME    = 'failed_regrouping'
ORACLE_SUBDIR   = 'oracles'


# ── Program subsets (shared with RQ1_table.py) ────────────────────────────────

def _rq1_subsets() -> tuple[set[str], set[str]]:
    """NJR_25 and XCORPUS_SUBSET, imported from RQ1_table.py so they stay in sync."""
    sys.path.insert(0, str(PAPER_DIR))
    try:
        from RQ1_table import NJR_25, XCORPUS_SUBSET  # type: ignore
        return set(NJR_25), set(XCORPUS_SUBSET)
    except Exception as e:
        print(f"! could not import subsets from RQ1_table.py ({e}) — using --subset all")
        return set(), set()


# ── Records ───────────────────────────────────────────────────────────────────

@dataclass
class Failed:
    tool:        str
    bench:       str
    program:     str
    algorithm:   str
    boundary_id: str
    index:       int
    dir:         Path
    loose:       tuple[str, str, str, str]
    strict:      Optional[tuple[str, str, str, str, str, str]]
    reason:      str


@dataclass
class Success:
    tool:          str
    bench:         str
    program:       str
    algorithm:     str
    boundary_id:   str
    dir:           Path
    pattern_class: str
    agent_jar:     Optional[Path]
    # program_name -> [(oracle_path, source_tag)]
    oracles:       dict[str, list[tuple[Path, str]]] = field(default_factory=dict)


@dataclass
class Verdict:
    status:     str = 'still_failed'   # regrouped | still_failed | no_candidates
    by:         Optional[str] = None   # boundary_id of the grouping success
    by_program: Optional[str] = None
    oracle:     Optional[str] = None
    source:     Optional[str] = None   # existing_oracle | new_run
    strict:     bool = False           # descriptor-aware match too?
    checked:    int = 0                # oracles compared against this boundary


# ── Symptom / edge keys ───────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.replace('/', '.')


def _split_member(raw: str) -> Optional[tuple[str, str, str]]:
    """'a.b.C.m(Ldesc;)V' -> ('a.b.C', 'm', '(Ldesc;)V'), all dot-normalised."""
    head, sep, rest = raw.partition('(')
    if '.' not in head:
        return None
    cls, meth = head.rsplit('.', 1)
    if not cls or not meth:
        return None
    return _norm(cls), meth, _norm(sep + rest) if sep else ''


def extract_symptoms(oracle_path: Path) -> tuple[set, set]:
    """
    Parse an oracle JSON and return (loose, strict) symptom sets:
      loose  = {(caller_class, caller_method, callee_class, callee_method)}
      strict = loose + caller/callee descriptors
    Mirrors pipeline/utils.py::extract_symptoms for the loose set.
    """
    loose: set = set()
    strict: set = set()
    try:
        data = json.loads(oracle_path.read_text())
    except Exception:
        return loose, strict

    if not isinstance(data, dict):
        return loose, strict

    for rc_entry in data.values():
        if not isinstance(rc_entry, dict):
            continue
        for sym in rc_entry.get('symptoms', []) or []:
            caller = _split_member(str(sym.get('caller', '')))
            callee = _split_member(str(sym.get('callee', '')))
            if not caller or not callee:
                continue
            loose.add((caller[0], caller[1], callee[0], callee[1]))
            strict.add((caller[0], caller[1], caller[2], callee[0], callee[1], callee[2]))
    return loose, strict


def _member_key(m: dict) -> Optional[tuple[str, str, str]]:
    """A boundary's caller/missedCallee dict -> (class, method, descriptor)."""
    cls = str(m.get('declaringClass', '') or '')
    cls = _norm(cls.lstrip('L').rstrip(';'))
    meth = str(m.get('name', '') or '')
    if not cls or not meth:
        return None
    params = ''.join(str(p) for p in (m.get('parameterTypes') or []))
    ret = str(m.get('returnType', '') or '')
    desc = _norm(f"({params}){ret}") if ret else ''
    return cls, meth, desc


def boundary_keys(boundary: dict):
    """(loose, strict) keys for a boundary entry, or (None, None) if malformed."""
    caller = _member_key(boundary.get('caller', {}) or {})
    callee = _member_key(boundary.get('missedCallee', {}) or {})
    if not caller or not callee:
        return None, None
    loose = (caller[0], caller[1], callee[0], callee[1])
    strict = (caller[0], caller[1], caller[2], callee[0], callee[1], callee[2])
    return loose, (strict if caller[2] and callee[2] else None)


def derive_boundary_id(boundary: dict, index: int) -> str:
    """Replicates pipeline_orchestrator._boundary_id."""
    def short(cls: str) -> str:
        return cls.rstrip(';').split('/')[-1].lstrip('L') if cls else '?'

    caller = boundary.get('caller', {}) or {}
    missed = boundary.get('missedCallee', {}) or {}
    caller_label = f"{short(caller.get('declaringClass', ''))}.{caller.get('name', '?')}"
    missed_label = f"{short(missed.get('declaringClass', ''))}.{missed.get('name', '?')}"
    safe = f"{caller_label}__{missed_label}".replace('/', '.').replace(' ', '_')
    return f"b{index}_{safe}"


# ── Collection ────────────────────────────────────────────────────────────────

def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


_boundaries_cache: dict[Path, list] = {}


def load_boundaries(bench: str, program: str, tool_dir: str, algorithm: str) -> list:
    path = INPUT_DATA / bench / program / tool_dir / algorithm / 'boundaries.json'
    if path in _boundaries_cache:
        return _boundaries_cache[path]
    data = read_json(path)
    entries = (data or {}).get('boundaries', []) if isinstance(data, dict) else []
    _boundaries_cache[path] = entries
    return entries


def lookup_boundary(bench: str, program: str, tool_dir: str, algorithm: str,
                    index: int, boundary_id: str) -> Optional[dict]:
    """
    Fetch the boundary entry by original index, validating against boundary_id.
    Falls back to an id-based scan when the index does not line up.
    """
    entries = load_boundaries(bench, program, tool_dir, algorithm)
    if not entries:
        return None
    if 0 <= index < len(entries):
        b = entries[index]
        if derive_boundary_id(b, index) == boundary_id:
            return b
    for i, b in enumerate(entries):
        if derive_boundary_id(b, i) == boundary_id:
            return b
    return None


def results_dir(root: Path, tool: str, bench: str) -> Path:
    return root / f"results_{tool}_{bench}"


def find_agent_jar(boundary_dir: Path) -> Optional[Path]:
    """Mirrors pipeline/utils.py::find_agent_jar_from_dir."""
    target = boundary_dir / 'test_case' / 'reproducing' / 'instrumentation' / 'target'
    if not target.exists():
        return None
    jars = [j for j in target.glob('*.jar') if not j.name.startswith('original-')]
    return jars[0] if jars else None


def collect(root: Path, tool: str, keep: callable) -> tuple[list[Failed], list[Success]]:
    """Walk results_<tool>_<bench>/<program>/<TOOL>/<algo>/<boundary>/ once."""
    failed:    list[Failed] = []
    successes: list[Success] = []

    for bench in BENCHS:
        d = results_dir(root, tool, bench)
        if not d.exists():
            continue
        for edge_dir in sorted(d.glob('*/*/*/*')):
            if not edge_dir.is_dir():
                continue
            result = read_json(edge_dir / PIPELINE_FILE)
            if result is None:
                continue  # pending — not our business

            program   = edge_dir.parts[-4]
            tool_dir  = edge_dir.parts[-3]
            algorithm = edge_dir.parts[-2]
            b_id      = edge_dir.name
            if not keep(bench, program):
                continue

            if result.get('success', False):
                report = read_json(edge_dir / REPORT_FILE) or {}
                s = Success(
                    tool=tool, bench=bench, program=program, algorithm=algorithm,
                    boundary_id=b_id, dir=edge_dir,
                    pattern_class=str(report.get('pattern_class', '') or ''),
                    agent_jar=find_agent_jar(edge_dir),
                )
                # oracle_target.json is this success's oracle on its own program.
                target = edge_dir / TARGET_ORACLE
                if target.exists():
                    s.oracles.setdefault(program, []).append((target, 'target'))
                for sub, tag in ORACLE_DIRS.items():
                    for f in sorted((edge_dir / sub).glob('oracle_*.json')):
                        prog = f.name[len('oracle_'):-len('.json')]
                        s.oracles.setdefault(prog, []).append((f, tag))
                successes.append(s)
                continue

            identity = result.get('identity', {}) or {}
            index    = identity.get('boundary_index', -1)
            boundary = lookup_boundary(bench, program, tool_dir, algorithm, index, b_id)
            if boundary is None:
                print(f"  ! {tool}/{bench}/{program}/{b_id}: boundary entry not found — skipped")
                continue
            loose, strict = boundary_keys(boundary)
            if loose is None:
                print(f"  ! {tool}/{bench}/{program}/{b_id}: malformed caller/callee — skipped")
                continue
            failed.append(Failed(
                tool=tool, bench=bench, program=program, algorithm=algorithm,
                boundary_id=b_id, index=index, dir=edge_dir,
                loose=loose, strict=strict,
                reason=str(result.get('reason', ''))[:400],
            ))

    return failed, successes


# ── Config lookup (for the oracle runs) ───────────────────────────────────────

def experiment_oracle_path(oracle_root: Path, s: 'Success', program: str) -> Path:
    """Where this script stores a fresh oracle — outside the results tree."""
    return oracle_root / f"{s.program}__{s.boundary_id}" / f"oracle_{program}.json"


def attach_experiment_oracles(successes: list['Success'], oracle_root: Path) -> int:
    """Pick up oracles written by earlier invocations of this script."""
    n = 0
    for s in successes:
        d = oracle_root / f"{s.program}__{s.boundary_id}"
        if not d.exists():
            continue
        for f in sorted(d.glob('oracle_*.json')):
            prog = f.name[len('oracle_'):-len('.json')]
            s.oracles.setdefault(prog, []).append((f, 'failregroup'))
            n += 1
    return n


def load_configs() -> dict[tuple[str, str], dict]:
    """(tool_lower, program_name) -> analysis entry, with paths resolved."""
    table: dict[tuple[str, str], dict] = {}
    for cfg in sorted(CONFIG_DIR.glob('pipeline_config_*.json')):
        data = read_json(cfg)
        if not isinstance(data, dict):
            continue
        for entry in data.get('analyses', []) or []:
            tool = str(entry.get('sa_tool_name', '')).lower()
            prog = entry.get('program_name')
            if tool and prog:
                table.setdefault((tool, prog), entry)
    return table


def resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (AGENTIC_DIR / p).resolve()


def build_classpath(program_jar_path: str) -> Optional[str]:
    """Mirrors pipeline/utils.py::build_classpath."""
    p = resolve(program_jar_path)
    if not p.exists():
        return None
    if p.is_file():
        return str(p)
    jars = list(p.glob('*.jar'))
    return ':'.join(str(j) for j in jars) if jars else None


def run_oracle(agent_jar: Path, program_jar_path: str, main_class: str,
               out_path: Path, timeout: int) -> tuple[bool, str]:
    """
    Mirrors pipeline/utils.py::run_oracle_on_program.
    Returns (oracle_written, status) with status in
    {ok, crashed, timeout, no_classpath, error}.
    """
    classpath = build_classpath(program_jar_path)
    if not classpath:
        return False, 'no_classpath'

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['java', f"-javaagent:{agent_jar.resolve()}={out_path}",
           '-cp', classpath, main_class]

    tmp = tempfile.mkdtemp(prefix='failregroup_')
    try:
        proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, check=False, cwd=tmp)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        return out_path.exists(), 'timeout'
    except Exception:
        return out_path.exists(), 'error'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not out_path.exists():
        return False, 'crashed' if rc != 0 else 'no_oracle'
    # A non-zero exit with an oracle written is still usable (matches the
    # pipeline, which records `crashed` but keeps the file).
    return True, 'ok' if rc == 0 else 'crashed_with_oracle'


# ── Matching ──────────────────────────────────────────────────────────────────

_symptom_cache: dict[Path, tuple[set, set]] = {}


def symptoms_of(path: Path) -> tuple[set, set]:
    if path not in _symptom_cache:
        _symptom_cache[path] = extract_symptoms(path)
    return _symptom_cache[path]


def check(failed: Failed, oracle: Path) -> tuple[bool, bool]:
    """(loose_hit, strict_hit) for one failed boundary against one oracle."""
    loose, strict = symptoms_of(oracle)
    hit = failed.loose in loose
    strict_hit = bool(failed.strict and failed.strict in strict)
    return hit, strict_hit


def rank_candidates(f_program: str, successes: list[Success],
                    pattern_hint: str = '') -> list[Success]:
    """Same program first, then same pattern_class, then the rest."""
    def key(s: Success):
        return (
            0 if s.program == f_program else 1,
            0 if (pattern_hint and s.pattern_class == pattern_hint) else 1,
            s.program, s.boundary_id,
        )
    return sorted(successes, key=key)


# ── Phase 1: existing oracles, no JVM ─────────────────────────────────────────

def dry_pass(failed: list[Failed], successes: list[Success]) -> dict[str, Verdict]:
    verdicts: dict[str, Verdict] = {}
    for f in failed:
        v = Verdict()
        for s in rank_candidates(f.program, successes):
            for oracle, _tag in s.oracles.get(f.program, []):
                v.checked += 1
                hit, strict_hit = check(f, oracle)
                if hit:
                    v.status, v.by, v.by_program = 'regrouped', s.boundary_id, s.program
                    v.oracle, v.source, v.strict = str(oracle), 'existing_oracle', strict_hit
                    break
            if v.status == 'regrouped':
                break
        if v.status != 'regrouped' and v.checked == 0:
            v.status = 'no_candidates'
        verdicts[f.boundary_id + '@' + f.program] = v
    return verdicts


# ── Phase 2: fill the gap with fresh oracle runs ──────────────────────────────

def run_pass(failed: list[Failed], successes: list[Success],
             verdicts: dict[str, Verdict], configs: dict, oracle_root: Path,
             max_runs: int, jobs: int, timeout: int) -> list[dict]:
    """
    Batched per program: one JVM run of (success, program) covers every
    unresolved failed boundary of that program. Short-circuits per program.
    """
    by_program: dict[str, list[Failed]] = {}
    for f in failed:
        if verdicts[f.boundary_id + '@' + f.program].status != 'regrouped':
            by_program.setdefault(f.program, []).append(f)

    budget = {'left': max_runs if max_runs > 0 else 10 ** 9}
    run_log: list[dict] = []

    def work(program: str, pending: list[Failed]) -> list[dict]:
        tool = pending[0].tool
        entry = configs.get((tool, program))
        if not entry:
            return [{'program': program, 'status': 'skipped_no_config'}]
        jar_path, main_class = entry.get('program_jar_path'), entry.get('main_class')
        if not jar_path or not main_class:
            return [{'program': program, 'status': 'skipped_no_config'}]

        local_log: list[dict] = []
        unresolved = list(pending)
        hint = ''
        for s in rank_candidates(program, successes, hint):
            if not unresolved:
                break
            if program in s.oracles:
                continue  # already replayed in the dry pass
            if not s.agent_jar:
                continue
            if budget['left'] <= 0:
                local_log.append({'program': program, 'status': 'budget_exhausted'})
                break
            budget['left'] -= 1

            out = experiment_oracle_path(oracle_root, s, program)
            ok, status = run_oracle(s.agent_jar, jar_path, main_class, out, timeout)
            local_log.append({
                'program': program, 'by': s.boundary_id, 'by_program': s.program,
                'oracle': str(out), 'status': status,
            })
            print(f"    run {s.boundary_id} -> {program}: {status}")
            if not ok:
                continue

            still: list[Failed] = []
            for f in unresolved:
                hit, strict_hit = check(f, out)
                v = verdicts[f.boundary_id + '@' + f.program]
                v.checked += 1
                if hit:
                    v.status, v.by, v.by_program = 'regrouped', s.boundary_id, s.program
                    v.oracle, v.source, v.strict = str(out), 'new_run', strict_hit
                    print(f"      + regrouped {f.boundary_id}")
                else:
                    still.append(f)
            unresolved = still
        return local_log

    items = sorted(by_program.items())
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for logs in pool.map(lambda kv: work(*kv), items):
                run_log.extend(logs)
    else:
        for program, pending in items:
            run_log.extend(work(program, pending))
    return run_log


# ── Reporting ─────────────────────────────────────────────────────────────────

def report(tool: str, failed: list[Failed], successes: list[Success],
           verdicts: dict[str, Verdict], run_log: list[dict], out_dir: Path,
           mode: str, subset: str) -> dict:
    regrouped = [f for f in failed
                 if verdicts[f.boundary_id + '@' + f.program].status == 'regrouped']
    strict_n = sum(1 for f in regrouped
                   if verdicts[f.boundary_id + '@' + f.program].strict)

    payload = {
        'tool':             tool,
        'mode':             mode,
        'subset':           subset,
        'failed_total':     len(failed),
        'successes_total':  len(successes),
        'regrouped':        len(regrouped),
        'regrouped_strict': strict_n,
        'still_failed':     len(failed) - len(regrouped),
        'boundaries': [
            {
                'program':     f.program,
                'benchmark':   f.bench,
                'boundary_id': f.boundary_id,
                'edge':        f"{f.loose[0]}.{f.loose[1]} -> {f.loose[2]}.{f.loose[3]}",
                'dir':         str(f.dir),
                'failure_reason': f.reason,
                **vars(verdicts[f.boundary_id + '@' + f.program]),
            }
            for f in failed
        ],
        'oracle_runs': run_log,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"failed_regrouping_{tool}_{subset}.json"
    out_path.write_text(json.dumps(payload, indent=2))

    pct = (100.0 * len(regrouped) / len(failed)) if failed else 0.0
    print(f"  {tool:5s} failed={len(failed):4d}  regrouped={len(regrouped):4d} "
          f"({pct:5.1f}%)  strict={strict_n:4d}  "
          f"still_failed={len(failed) - len(regrouped):4d}   -> {out_path}")
    return payload


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--results-root', type=Path, default=OUTPUT_PATH)
    ap.add_argument('--tool', choices=TOOLS, action='append',
                    help='restrict to a tool (repeatable); default: all')
    ap.add_argument('--subset', choices=['rq1', 'all'], default='rq1',
                    help="'rq1' = the program subsets RQ1_table.py reports on")
    ap.add_argument('--dry-run', action='store_true',
                    help='replay existing oracles only (default when --run is absent)')
    ap.add_argument('--run', action='store_true',
                    help='also run missing (success, program) instrumentation pairs')
    ap.add_argument('--max-runs', type=int, default=0,
                    help='cap on fresh oracle runs per tool (0 = unlimited)')
    ap.add_argument('--jobs', type=int, default=1,
                    help='parallel programs during --run')
    ap.add_argument('--timeout', type=int, default=3600,
                    help='per-oracle-run timeout in seconds')
    args = ap.parse_args()

    root  = args.results_root
    tools = args.tool or TOOLS
    mode  = 'run' if args.run else 'dry-run'

    if args.subset == 'rq1':
        njr_25, xcorpus_subset = _rq1_subsets()
        cats_programs: set[str] = set()
        for tool in TOOLS:
            d = results_dir(root, tool, 'cats')
            if d.exists():
                cats_programs.update(p.name for p in d.iterdir() if p.is_dir())

        def keep(bench: str, program: str) -> bool:
            if bench == 'njr':
                return program in njr_25
            if bench == 'cats':
                return program in cats_programs
            if bench == 'xcorpus':
                return program in xcorpus_subset
            return False
    else:
        def keep(_bench: str, _program: str) -> bool:
            return True

    configs     = load_configs() if args.run else {}
    out_dir     = root / OUT_DIR_NAME
    oracle_root = out_dir / ORACLE_SUBDIR
    summary: dict[str, dict] = {}

    print(f"mode={mode}  subset={args.subset}  root={root}\n")
    for tool in tools:
        print(f"[{tool}] collecting …")
        failed, successes = collect(root, tool, keep)
        if not failed:
            print(f"  {tool}: no failed boundaries — skipping\n")
            continue

        reused = attach_experiment_oracles(successes, oracle_root)
        if reused:
            print(f"  reusing {reused} oracle(s) from earlier runs of this script")

        verdicts = dry_pass(failed, successes)
        dry_hits = sum(1 for v in verdicts.values() if v.status == 'regrouped')
        print(f"  existing oracles: {dry_hits}/{len(failed)} regrouped "
              f"({len(successes)} successes available)")

        run_log: list[dict] = []
        if args.run:
            run_log = run_pass(failed, successes, verdicts, configs, oracle_root,
                               args.max_runs, args.jobs, args.timeout)

        summary[tool] = report(tool, failed, successes, verdicts, run_log,
                               out_dir, mode, args.subset)
        print()

    if summary:
        tot_f = sum(s['failed_total'] for s in summary.values())
        tot_r = sum(s['regrouped'] for s in summary.values())
        tot_s = sum(s['regrouped_strict'] for s in summary.values())
        pct = (100.0 * tot_r / tot_f) if tot_f else 0.0
        print("=== TOTAL ===")
        print(f"  failed={tot_f}  regrouped={tot_r} ({pct:.1f}%)  "
              f"strict={tot_s}  still_failed={tot_f - tot_r}")
        (out_dir / f'failed_regrouping_summary_{args.subset}.json').write_text(json.dumps(
            {t: {k: v for k, v in s.items() if k != 'boundaries'}
             for t, s in summary.items()}, indent=2))


if __name__ == '__main__':
    main()
