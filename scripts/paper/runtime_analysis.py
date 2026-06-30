#!/usr/bin/env python3
"""
runtime_analysis.py
===================
Average wall-clock time (minutes) needed to analyze each boundary.

For every pipeline.log under the results trees (same traversal as
RQ1_feedback.py), the per-boundary duration is the difference between the last
and the first timestamped log line:

    duration = last_timestamp - first_timestamp

Log lines start with "YYYY-MM-DD HH:MM:SS,mmm". Lines without a timestamp
(multi-line agent output) are ignored when locating the first/last stamp.

Reported overall, per tool, and per (tool, benchmark).
"""

from pathlib import Path
from datetime import datetime
from collections import defaultdict
import json
import re
import statistics

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

LOG_FILE      = "pipeline.log"
PIPELINE_FILE = "pipeline_result.json"


def is_successful(edge_dir: Path) -> bool:
    """True if pipeline_result.json exists and success == true."""
    try:
        return json.loads((edge_dir / PIPELINE_FILE).read_text()).get("success", False)
    except Exception:
        return False

TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})')
TS_FMT = "%Y-%m-%d %H:%M:%S,%f"


def parse_ts(line: str):
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), TS_FMT)
    except ValueError:
        return None


def log_duration_minutes(log_path: Path):
    """Return wall-clock minutes between first and last timestamped line, or None."""
    first = last = None
    try:
        with open(log_path, "r", errors="ignore") as f:
            for line in f:
                ts = parse_ts(line)
                if ts is None:
                    continue
                if first is None:
                    first = ts
                last = ts
    except Exception:
        return None
    if first is None or last is None or last < first:
        return None
    return (last - first).total_seconds() / 60.0


def results_dir(tool: str, bench: str) -> Path:
    return OUTPUT_PATH / f"results_{tool}_{bench}"


def fmt_stats(durations):
    if not durations:
        return "n=0"
    return (f"n={len(durations):4d}  "
            f"mean={statistics.mean(durations):6.1f} min  "
            f"median={statistics.median(durations):6.1f} min  "
            f"min={min(durations):5.1f}  max={max(durations):6.1f}  "
            f"total={sum(durations)/60.0:6.1f} h")


def main():
    by_tool_bench = defaultdict(list)
    by_tool = defaultdict(list)
    all_durations = []
    skipped = 0

    for tool in TOOLS:
        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            for log_path in d.glob("*/*/*/*/" + LOG_FILE):
                if not is_successful(log_path.parent):
                    continue
                dur = log_duration_minutes(log_path)
                if dur is None:
                    skipped += 1
                    continue
                by_tool_bench[(tool, bench)].append(dur)
                by_tool[tool].append(dur)
                all_durations.append(dur)

    print("=" * 78)
    print("PER-BOUNDARY ANALYSIS TIME (wall-clock, from pipeline.log)")
    print("  [SUCCESSFUL boundaries only: pipeline_result.json success == true]")
    print("=" * 78)

    print("\n--- Per tool / benchmark ---")
    for tool in TOOLS:
        for bench in BENCHS:
            ds = by_tool_bench[(tool, bench)]
            print(f"{tool:5s} {bench:8s}  {fmt_stats(ds)}")

    print("\n--- Per tool ---")
    for tool in TOOLS:
        print(f"{tool:5s} {'':9s} {fmt_stats(by_tool[tool])}")

    print("\n--- Overall ---")
    print(f"{'ALL':14s} {fmt_stats(all_durations)}")
    if all_durations:
        print(f"\nAverage minutes per boundary: {statistics.mean(all_durations):.1f}")
    if skipped:
        print(f"(skipped {skipped} logs with no/unparseable timestamps)")


if __name__ == "__main__":
    main()
