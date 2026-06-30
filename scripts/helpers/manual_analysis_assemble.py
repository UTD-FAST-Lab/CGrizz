#!/usr/bin/env python3
"""
manual_analysis_assemble.py
===========================
Assembles the manual root-cause grouping of the 151 "reasoned" (successful)
boundaries into per-tool JSON + CSV under:
    /20TB/mohammad/agentic_cg_unsoundness_icse27/failure_analysis/manual_analysis/<tool>/

Inputs (produced earlier):
  - reasoned_inventory.json : every reasoned boundary with unsound_count and
    general_log_flag ([UNSOUNDNESS] count > 30 in test_case/reproducing/staticcg/log.txt)
  - <tool>_clusters.json    : per-tool root-cause clustering (canonical_group,
    sub_mechanism, rationale), judged from each unsoundness_report.json.

For each tool it computes:
  - total reasoned
  - distinct root causes (# canonical groups)
  - duplicates = total - distinct  (boundaries sharing a root cause with another)
  - per-group counts, and a finer (group, sub_mechanism) breakdown
  - # boundaries whose execution log is "very general" (general_log_flag)
"""

from pathlib import Path
from collections import defaultdict, Counter
import csv
import html
import json

SCRATCH = Path("/tmp/claude-1014/-home-mohammad-projects-AgenticCGEval-agentic-workflow/"
               "2b515d27-9882-4710-a012-79d13c153817/scratchpad")
OUT_BASE = Path("/20TB/mohammad/agentic_cg_unsoundness_icse27/failure_analysis/manual_analysis")
TOOLS = ["wala", "opal", "soot"]


def load_json(p: Path):
    return json.loads(p.read_text())


def unesc(s):
    return html.unescape(s) if isinstance(s, str) else s


def main():
    inventory = load_json(SCRATCH / "reasoned_inventory.json")
    inv_by_relpath = {r["rel_path"]: r for r in inventory}

    grand = {}

    for tool in TOOLS:
        clusters = load_json(SCRATCH / f"{tool}_clusters.json")
        rows = []
        missing = []

        for c in clusters:
            rel_path = unesc(c["rel_path"])
            inv = inv_by_relpath.get(rel_path)
            if inv is None:
                missing.append(rel_path)
                inv = {}
            rows.append({
                "tool": tool,
                "bench": inv.get("bench"),
                "program": unesc(c["program"]),
                "boundary_id": unesc(c["boundary_id"]),
                "caller": inv.get("caller"),
                "target": inv.get("target"),
                "pattern_class": c.get("pattern_class"),
                "canonical_group": c["canonical_group"],
                "sub_mechanism": c.get("sub_mechanism", ""),
                "unsound_count": inv.get("unsound_count"),
                "general_log_flag": bool(inv.get("general_log_flag")),
                "rationale": c.get("rationale", ""),
            })

        # ── duplicate flags ──
        # Coarse: same canonical_group. Fine: same (group, sub_mechanism).
        group_members = defaultdict(list)
        fine_members = defaultdict(list)
        for r in rows:
            group_members[r["canonical_group"]].append(r)
            fine_members[(r["canonical_group"], r["sub_mechanism"])].append(r)

        for r in rows:
            g = r["canonical_group"]
            fk = (g, r["sub_mechanism"])
            r["group_size"] = len(group_members[g])
            r["fine_group_size"] = len(fine_members[fk])

        # Mark duplicates: within each group, first (sorted) is the representative.
        for g, members in group_members.items():
            members_sorted = sorted(members, key=lambda r: (r["program"], r["boundary_id"]))
            for i, r in enumerate(members_sorted):
                r["is_duplicate_by_group"] = (i > 0)
        for fk, members in fine_members.items():
            members_sorted = sorted(members, key=lambda r: (r["program"], r["boundary_id"]))
            for i, r in enumerate(members_sorted):
                r["is_duplicate_by_mechanism"] = (i > 0)

        total = len(rows)
        distinct_groups = len(group_members)
        distinct_mechs = len(fine_members)
        dup_by_group = total - distinct_groups
        dup_by_mech = total - distinct_mechs
        general = sum(1 for r in rows if r["general_log_flag"])

        group_counts = {g: len(m) for g, m in
                        sorted(group_members.items(), key=lambda kv: -len(kv[1]))}

        summary = {
            "tool": tool,
            "total_reasoned": total,
            "distinct_root_causes": distinct_groups,
            "duplicates_by_group": dup_by_group,
            "distinct_mechanisms": distinct_mechs,
            "duplicates_by_mechanism": dup_by_mech,
            "general_execution_log_count": general,
            "general_execution_log_threshold": "[UNSOUNDNESS] entries > 30",
            "group_counts": group_counts,
            "missing_from_inventory": missing,
        }
        grand[tool] = summary

        # ── write per-tool outputs ──
        tdir = OUT_BASE / tool
        tdir.mkdir(parents=True, exist_ok=True)

        out = {"summary": summary, "boundaries": rows}
        (tdir / "grouping.json").write_text(json.dumps(out, indent=2))

        csv_fields = [
            "tool", "bench", "program", "boundary_id", "caller", "target",
            "pattern_class", "canonical_group", "sub_mechanism",
            "group_size", "is_duplicate_by_group",
            "fine_group_size", "is_duplicate_by_mechanism",
            "unsound_count", "general_log_flag", "rationale",
        ]
        with open(tdir / "grouping.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            w.writeheader()
            for r in sorted(rows, key=lambda r: (r["canonical_group"],
                                                 r["sub_mechanism"],
                                                 r["program"], r["boundary_id"])):
                w.writerow(r)

        print(f"=== {tool.upper()} ===")
        print(f"  total reasoned          : {total}")
        print(f"  distinct root causes    : {distinct_groups}  (duplicates by group: {dup_by_group})")
        print(f"  distinct mechanisms     : {distinct_mechs}  (duplicates by mechanism: {dup_by_mech})")
        print(f"  general execution logs  : {general}  ([UNSOUNDNESS] > 30)")
        print(f"  group counts            : {group_counts}")
        if missing:
            print(f"  WARNING missing from inventory: {len(missing)} -> {missing}")
        print(f"  -> {tdir}/grouping.json , grouping.csv")
        print()

    (OUT_BASE / "summary_all_tools.json").write_text(json.dumps(grand, indent=2))
    print(f"Combined summary -> {OUT_BASE}/summary_all_tools.json")


if __name__ == "__main__":
    main()
