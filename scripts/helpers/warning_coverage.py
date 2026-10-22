"""
Which missed edges do the [UNSOUNDNESS] warnings of annotated SA tools explain? (#14)

Matches warnings to boundaries by caller and call instruction (pc, else line)
and reports three sets: explained boundaries, boundaries without a warning,
and warned instructions without a boundary (see agentic_workflow/pipeline/edge_mapping.py).

Two ways to use it:

  # one program: the annotated tool's trace(s) and the program's boundaries
  python scripts/helpers/warning_coverage.py --trace log.txt [log2.txt ...] --boundaries boundaries.json

  # a results directory: every reasoned boundary's test case
  # (test_case/reproducing/staticcg/log.txt vs. test_case/reproducing/boundary/<tool>/<algo>/boundaries.json)
  python scripts/helpers/warning_coverage.py --results ../results

Add --json out.json for the full lists.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agentic_workflow"))

from pipeline.edge_mapping import map_files  # noqa: E402


def from_results(root: Path) -> list[dict]:
    rows = []
    for result in sorted(root.glob("*/*/*/*/pipeline_result.json")):
        d = result.parent
        program, tool, algo, boundary = d.parts[-4:]
        tc = d / "test_case" / "reproducing"
        trace, boundaries = tc / "staticcg" / "log.txt", tc / "boundary" / tool / algo / "boundaries.json"
        if not trace.exists() or not boundaries.exists():
            continue
        m = map_files([trace], boundaries)
        rows.append({"program": program, "tool": tool, "algorithm": algo, "boundary": boundary, **m})
    return rows


def print_mapping(m: dict) -> None:
    c = m["counts"]
    print(f"boundaries: {c['boundaries']}  explained: {c['explained']}  without warning: {c['boundary_only']}  "
          f"warned instructions: {c['warned_instructions']}  without boundary: {c['warning_only_instructions']}")
    for title, key in (("Explained", "explained"), ("Boundaries without a warning", "boundary_only"),
                       ("Warnings without a boundary", "warning_only")):
        items = m[key]
        if items:
            print(f"\n{title} ({len(items)}):")
            for it in items[:25]:
                print("  " + (f"{it['boundary']}  [{it['description']}]" if isinstance(it, dict) else it))
            if len(items) > 25:
                print(f"  … {len(items) - 25} more (--json for all)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace", nargs="+", type=Path, help="annotated tool output(s) with [UNSOUNDNESS] lines")
    ap.add_argument("--boundaries", type=Path, help="boundaries.json of the same program/tool/algorithm")
    ap.add_argument("--results", type=Path, help="a pipeline results directory")
    ap.add_argument("--json", type=Path, help="write the full mapping as JSON")
    args = ap.parse_args()

    if args.results:
        rows = from_results(args.results)
        print("| program | tool | boundary | test-case boundaries | explained | without warning | warnings without boundary |")
        print("|---|---|---|---|---|---|---|")
        for r in rows:
            c = r["counts"]
            print(f"| {r['program']} | {r['tool']}/{r['algorithm']} | {r['boundary']} | {c['boundaries']} "
                  f"| {c['explained']} | {c['boundary_only']} | {c['warning_only_instructions']} |")
        total = {k: sum(r["counts"][k] for r in rows) for k in ("boundaries", "explained", "boundary_only")}
        print(f"\ntotal over {len(rows)} reasoned boundaries: {total}")
        out = rows
    elif args.trace and args.boundaries:
        out = map_files(args.trace, args.boundaries)
        print_mapping(out)
    else:
        ap.error("give --trace and --boundaries, or --results")
    if args.json:
        args.json.write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
