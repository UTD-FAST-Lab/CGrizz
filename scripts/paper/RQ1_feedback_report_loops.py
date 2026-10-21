#!/usr/bin/env python3
"""
RQ1_feedback_report_loops.py
============================
For every SUCCESSFUL boundary: how many times did each feedback kind send the
pipeline back to REPORT GENERATION (stage 3) before it eventually succeeded?

Only report revisions count. Local retries are ignored:
    * oracle refinement attempts (instrumentation re-generated, report kept)
    * annotation / trace retries inside the annotation loop (report kept)

A report revision is an outer-loop iteration ("──── Loop N" in pipeline.log).
It is attributed to the feedback that triggered it, i.e. the last decision
marker logged before the loop header:

    "Escalating to report generator"        -> oracle   (refinements exhausted)
    "Not reproduced. Feedback recorded"     -> boundary (reproduction check)
    "Trace check failed. Feedback recorded" -> trace    (annotation loop exhausted,
    "Annotation failed. Feedback recorded"  -> agent     last inner failure kind)

Only the last "PIPELINE START" segment of a log is read (a boundary re-run
appends to the same pipeline.log). Program subsets are those of RQ1_feedback.py.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from RQ1_feedback import (BENCHS, BENCH_LABEL, LOG_FILE, NJR_25, PIPELINE_FILE, TOOLS,
                          TOOL_LABEL, XCORPUS_SUBSET, discover_cats_programs, edge_dirs,
                          read_json, results_dir)

TRIGGERS = [
    ("Escalating to report generator",        "oracle"),
    ("Not reproduced. Feedback recorded",     "boundary"),
    ("Trace check failed. Feedback recorded", "trace"),
    ("Annotation failed. Feedback recorded",  "agent"),
]
KINDS = ["oracle", "boundary", "trace", "agent"]
LOOP_HEADER = "──── Loop "

OUT_JSON = Path(__file__).resolve().parent / "RQ1_feedback_report_loops.json"


def report_revisions(log_path: Path) -> list[str]:
    """Feedback kinds that triggered each report revision, in order."""
    try:
        lines = log_path.read_text(errors="ignore").splitlines()
    except Exception:
        return []
    starts = [i for i, l in enumerate(lines) if "PIPELINE START" in l]
    lines = lines[starts[-1]:] if starts else lines

    revisions, last = [], None
    for line in lines:
        if LOOP_HEADER in line:
            revisions.append(last or "unknown")
            last = None
            continue
        for marker, kind in TRIGGERS:
            if marker in line:
                last = kind
                break
    return revisions


def main():
    cats_programs = discover_cats_programs()
    subset = {'njr': NJR_25, 'cats': cats_programs, 'xcorpus': XCORPUS_SUBSET}

    # (tool, bench) -> Counter of report revisions per feedback kind
    revs = defaultdict(Counter)
    # (tool, bench) -> successful cases / cases with >= 1 report revision
    n_succ, n_revised = Counter(), Counter()
    # (tool, bench) -> Counter of full paths, e.g. "boundary > success"
    paths = defaultdict(Counter)
    per_case = []

    for tool in TOOLS:
        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            for program_dir in d.iterdir():
                if not program_dir.is_dir() or program_dir.name not in subset[bench]:
                    continue
                for edge_dir in edge_dirs(program_dir):
                    data = read_json(edge_dir / PIPELINE_FILE)
                    if not data or not data.get("success", False):
                        continue
                    seq = report_revisions(edge_dir / LOG_FILE)
                    key = (tool, bench)
                    n_succ[key] += 1
                    n_revised[key] += bool(seq)
                    revs[key].update(seq)
                    paths[key][" > ".join(seq + ["success"])] += 1
                    per_case.append({"tool": tool, "bench": bench, "program": program_dir.name,
                                     "edge": str(edge_dir.relative_to(program_dir)),
                                     "report_revisions": seq})

    _print_tables(revs, n_succ, n_revised)
    _print_paths(paths)
    OUT_JSON.write_text(json.dumps(per_case, indent=2))
    print(f"\nPer-case revision sequences written to {OUT_JSON}")


# ── Output helpers ────────────────────────────────────────────────────────────

def _row(label, c, n, r):
    total = sum(c[k] for k in KINDS) + c["unknown"]
    extra = f"  unknown={c['unknown']}" if c["unknown"] else ""
    return (f"{label:16s} {c['oracle']:7d} {c['boundary']:9d} {c['trace']:6d} {c['agent']:6d} "
            f"{total:6d}   {n:5d} {r:8d}{extra}")


def _print_tables(revs, n_succ, n_revised):
    head = (f"{'':16s} {'Oracle':>7s} {'Boundary':>9s} {'Trace':>6s} {'Agent':>6s} "
            f"{'Total':>6s}   {'#succ':>5s} {'#revised':>8s}")
    print("=" * 80)
    print("REPORT REVISIONS BEFORE SUCCESS, by triggering feedback (local retries excluded)")
    print("=" * 80)

    print("\n-- Per tool (all benchmarks) --")
    print(head)
    grand, gn, gr = Counter(), 0, 0
    for tool in TOOLS:
        c, n, r = Counter(), 0, 0
        for bench in BENCHS:
            c += revs[(tool, bench)]; n += n_succ[(tool, bench)]; r += n_revised[(tool, bench)]
        grand += c; gn += n; gr += r
        print(_row(TOOL_LABEL[tool], c, n, r))
    print(_row("TOTAL", grand, gn, gr))

    print("\n-- Per benchmark x tool --")
    print(head)
    for bench in BENCHS:
        bc, bn, br = Counter(), 0, 0
        for tool in TOOLS:
            k = (tool, bench)
            bc += revs[k]; bn += n_succ[k]; br += n_revised[k]
            print(_row(f"{BENCH_LABEL[bench]} {TOOL_LABEL[tool]}", revs[k], n_succ[k], n_revised[k]))
        print(_row(f"{BENCH_LABEL[bench]} all", bc, bn, br))
        print()


def _print_paths(paths):
    print("-- Revision paths per tool (feedback that re-triggered the report, in order) --")
    for tool in TOOLS:
        agg = Counter()
        for bench in BENCHS:
            agg += paths[(tool, bench)]
        print(f"{TOOL_LABEL[tool]}: " + ", ".join(f"{p}: {n}" for p, n in agg.most_common()))


if __name__ == "__main__":
    main()
