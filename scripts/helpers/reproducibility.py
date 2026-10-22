"""
How reproducible are pipeline runs? (#5)

Compare two or more results directories produced from the same boundaries
(e.g. the same config run 3 times) and report, for every pair of runs:

  status          same outcome per boundary: reasoned / grouped / failed
  tool files      same tool files in toolLimitation.toolCodeEvidence.relevantFiles
  evidence files  same target-program files in evidence
  annotation      same annotated tool file (and file:line)
  grouping        co-grouped pairs that agree (Jaccard), and the Rand index over all pairs

All but status and grouping are compared for boundaries reasoned in both runs.
These are locations in the code, not labels, so new root causes compare the same
way as known ones. Files are compared as sets, without line numbers.

A boundary's group is the reasoned boundary that represents it: itself if it
was reasoned, the triggering boundary if it was pruned (pruning_report.json).

Usage:
    python scripts/helpers/reproducibility.py results_run1 results_run2 [results_run3 ...] [--json out.json]
"""

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

Key = tuple[str, str, str, str]  # program, tool, algorithm, boundary_id
_LOCATION = re.compile(r"^([^:\s]+):\d+")


def _files(entries) -> frozenset:
    """{'path/File.java', ...} from 'path/File.java:98 — note' entries."""
    out = set()
    for e in entries or []:
        m = _LOCATION.match(e.strip()) if isinstance(e, str) else None
        if m:
            out.add(m.group(1))
    return frozenset(out)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_run(root: Path) -> dict[Key, dict]:
    """key → {status, failure_kind, tool_files, evidence_files, annotation, group}"""
    run: dict[Key, dict] = {}
    pruned: dict[Key, Key] = {}
    for result_file in sorted(root.glob("*/*/*/*/pipeline_result.json")):
        d = result_file.parent
        key: Key = (d.parts[-4], d.parts[-3], d.parts[-2], d.parts[-1])
        res = _read(result_file)
        report = res.get("report") or {}
        if "tool_locations" not in report:  # results written before pipeline_result.json had the summary
            r = _read(d / "unsoundness_report.json")
            limitation = r.get("toolLimitation") or {}
            report = {"tool_locations": (limitation.get("toolCodeEvidence") or {}).get("relevantFiles"),
                      "evidence_locations": r.get("evidence")}
        ann = res.get("annotation") or {}
        run[key] = {
            "status": "reasoned" if res.get("success") else "failed",
            "failure_kind": res.get("failure_kind"),
            "tool_files": _files(report.get("tool_locations")) or None,
            "evidence_files": _files(report.get("evidence_locations")) or None,
            "annotation_file": ann.get("file"),
            "annotation_site": f"{ann.get('file')}:{ann.get('line')}" if ann.get("file") else None,
            "group": key,
        }
        for p in _read(d / "pruning_report.json").get("pruned_boundaries", []):
            pkey: Key = (p.get("program_name"), p.get("tool_name") or key[1], p.get("algorithm") or key[2],
                         p.get("boundary_id"))
            pruned.setdefault(pkey, key)
    for pkey, rep in pruned.items():
        if pkey not in run or run[pkey]["status"] != "reasoned":
            run[pkey] = {"status": "grouped", "group": rep}
    return run


def _agree(a: dict, b: dict, keys, field: str) -> tuple[int, int]:
    pairs = [(a[k].get(field), b[k].get(field)) for k in keys]
    pairs = [(x, y) for x, y in pairs if x is not None or y is not None]
    return sum(x == y for x, y in pairs), len(pairs)


def _grouping(a: dict, b: dict, keys: list[Key]) -> tuple[float, float, int]:
    def together(run, k1, k2):
        return run[k1]["group"] == run[k2]["group"]
    total = same = union = both = 0
    for k1, k2 in itertools.combinations(keys, 2):
        ta, tb = together(a, k1, k2), together(b, k1, k2)
        total += 1
        same += ta == tb
        if ta or tb:
            union += 1
            both += ta and tb
    jaccard = both / union if union else 1.0
    rand = same / total if total else 1.0
    return jaccard, rand, union


def compare(a: dict, b: dict) -> dict:
    common = sorted(set(a) & set(b))
    reasoned = [k for k in common if a[k]["status"] == b[k]["status"] == "reasoned"]
    out = {"boundaries": len(common), "reasoned_in_both": len(reasoned)}
    s, n = _agree(a, b, common, "status")
    out["status"] = (s, n)
    for field in ("tool_files", "evidence_files", "annotation_file", "annotation_site"):
        out[field] = _agree(a, b, reasoned, field)
    jaccard, rand, union = _grouping(a, b, common)
    out["grouping_jaccard"], out["grouping_rand"], out["co_grouped_pairs"] = jaccard, rand, union
    return out


def pct(x: tuple[int, int]) -> str:
    return f"{x[0]}/{x[1]} ({x[0] / x[1]:.0%})" if x[1] else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--json", type=Path, help="also write the numbers as JSON")
    args = ap.parse_args()
    if len(args.results) < 2:
        ap.error("give at least two results directories")

    runs = {str(r): load_run(r) for r in args.results}
    rows = []
    print("| runs | boundaries | status | tool files | evidence files | annotation file "
          "| annotation file:line | grouping (Jaccard) | grouping (Rand) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (na, a), (nb, b) in itertools.combinations(runs.items(), 2):
        c = compare(a, b)
        rows.append({"a": na, "b": nb, **c})
        print(f"| {Path(na).name} vs {Path(nb).name} | {c['boundaries']} | {pct(c['status'])} "
              f"| {pct(c['tool_files'])} | {pct(c['evidence_files'])} | {pct(c['annotation_file'])} "
              f"| {pct(c['annotation_site'])} | {c['grouping_jaccard']:.0%} ({c['co_grouped_pairs']} pairs) "
              f"| {c['grouping_rand']:.0%} |")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
