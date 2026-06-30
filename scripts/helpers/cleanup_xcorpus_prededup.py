#!/usr/bin/env python3
"""
cleanup_xcorpus_prededup.py
===========================
Remove the contamination left by pre-dedup runs against the *malformed* xcorpus
jars (super.jar without junit on the classpath -> empty oracles).

Those runs touched the njr/cats boundary result directories in two ways:

  1. pruning_report.json  -> oracle_runs got appended entries of the form
        {"program_name": "<xcorpus prog>", "status": "ok"|"skipped_no_config",
         "phase": "prededup", ...}
     (No pruned_boundaries were added — the malformed runs pruned nothing — so
      pruned_boundaries / pruned_count / triggered_by are left untouched.)

  2. oracles_prededup/    -> empty oracle_<xcorpus prog>.json files were written.

The SAME directories also hold *legitimate* prededup data for njr programs
(oracle_url<...>.json files and their oracle_runs entries). Those MUST be kept,
so this script targets ONLY the three xcorpus programs by name.

Re-running prededup APPENDS to pruning_report.json (it does not override), so
this cleanup must run first to avoid duplicate oracle_runs.

Usage
-----
    # dry run (default): report what would change, touch nothing
    python scripts/cleanup_xcorpus_prededup.py

    # actually apply the changes
    python scripts/cleanup_xcorpus_prededup.py --apply
"""

from pathlib import Path
import argparse
import json

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

# The malformed programs whose prededup artifacts must be removed.
XCORPUS_PROGRAMS = {
    'javacc-5.0',
    'nekohtml-1.9.14',
    'fitjava-1.1',
}

PRUNING_FILE = "pruning_report.json"
PREDEDUP_DIR = "oracles_prededup"


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def clean_pruning_report(report_path: Path, apply: bool):
    """
    Drop oracle_runs entries that are (phase == prededup) AND target an xcorpus
    program. Returns the number of entries removed (0 if nothing changed).
    """
    data = read_json(report_path)
    if data is None:
        return 0

    oracle_runs = data.get("oracle_runs", [])
    if not oracle_runs:
        return 0

    kept = [
        r for r in oracle_runs
        if not (r.get("phase") == "prededup"
                and r.get("program_name") in XCORPUS_PROGRAMS)
    ]
    removed = len(oracle_runs) - len(kept)
    if removed == 0:
        return 0

    # Safety: never touch pruned_boundaries here (malformed runs pruned nothing,
    # and any real njr prunings must be preserved).
    if apply:
        data["oracle_runs"] = kept
        report_path.write_text(json.dumps(data, indent=2))
    return removed


def clean_prededup_dir(dir_path: Path, apply: bool):
    """Delete oracle_<xcorpus>.json files. Returns number of files removed."""
    removed = 0
    for prog in XCORPUS_PROGRAMS:
        f = dir_path / f"oracle_{prog}.json"
        if f.exists():
            if apply:
                f.unlink()
            removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes (default: dry run, touch nothing).")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== xcorpus prededup cleanup [{mode}] ===")
    print(f"targets: {sorted(XCORPUS_PROGRAMS)}\n")

    reports_changed = 0
    oracle_runs_removed = 0
    dirs_touched = 0
    oracle_files_removed = 0

    # Scan every boundary result directory across all results_* trees.
    for report_path in sorted(OUTPUT_PATH.glob("results_*/*/*/*/*/" + PRUNING_FILE)):
        removed = clean_pruning_report(report_path, args.apply)
        if removed:
            reports_changed += 1
            oracle_runs_removed += removed
            print(f"  [report] -{removed:2d} oracle_runs  "
                  f"{report_path.relative_to(OUTPUT_PATH)}")

    for dir_path in sorted(OUTPUT_PATH.glob("results_*/*/*/*/*/" + PREDEDUP_DIR)):
        removed = clean_prededup_dir(dir_path, args.apply)
        if removed:
            dirs_touched += 1
            oracle_files_removed += removed
            print(f"  [oracles_prededup] -{removed} files  "
                  f"{dir_path.relative_to(OUTPUT_PATH)}")

    print("\n--- summary ---")
    print(f"pruning_report.json changed : {reports_changed}")
    print(f"  oracle_runs removed       : {oracle_runs_removed}")
    print(f"oracles_prededup dirs touched: {dirs_touched}")
    print(f"  oracle_<xcorpus>.json removed: {oracle_files_removed}")
    if not args.apply:
        print("\n(dry run — nothing was modified. Re-run with --apply to commit.)")


if __name__ == "__main__":
    main()
