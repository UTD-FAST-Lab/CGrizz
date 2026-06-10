from pathlib import Path
import os
import json
from collections import defaultdict

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')
TOOLS = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main():

    bench_stats = defaultdict(
        lambda: {
            "true": 0,
            "false": 0,
            "true_dedup": 0,
            "false_dedup": 0,
            "not_run": 0,
            "all_edges": 0,
        }
    )

    tool_stats = defaultdict(
        lambda: {
            "true": 0,
            "false": 0,
            "true_dedup": 0,
            "false_dedup": 0,
            "not_run": 0,
            "all_edges": 0,
        }
    )

    for tool in TOOLS:

        for bench in BENCHS:

            results_dir = OUTPUT_PATH / f"results_{tool}_{bench}"

            if not results_dir.exists():
                continue

            for program_dir in results_dir.iterdir():

                if not program_dir.is_dir():
                    continue

                #
                # program/X/Y/[edge]
                #
                edge_dirs = [
                    p for p in program_dir.glob("*/*/*")
                    if p.is_dir()
                ]


                bench_stats[(tool, bench)]["all_edges"] += len(edge_dirs)
                tool_stats[tool]["all_edges"] += len(edge_dirs)
                for edge_dir in edge_dirs:

                    pipeline_file = edge_dir / "pipeline_result.json"
                    pruning_file = edge_dir / "pruning_report.json"

                    #
                    # Edge was never processed
                    #
                    if not pipeline_file.exists():
                        bench_stats[(tool, bench)]["not_run"] += 1
                        tool_stats[tool]["not_run"] += 1
                        continue

                    pipeline_data = read_json(pipeline_file)

                    if pipeline_data is None:
                        bench_stats[(tool, bench)]["not_run"] += 1
                        tool_stats[tool]["not_run"] += 1
                        continue

                    success = pipeline_data.get("success", False)

                    pruned_count = 0
                    if pruning_file.exists():
                        pruning_data = read_json(pruning_file)

                        if pruning_data is not None:
                            pruned_count = pruning_data.get(
                                "pruned_count", 0
                            )

                    if success:
                        bench_stats[(tool, bench)]["true"] += 1
                        bench_stats[(tool, bench)]["true_dedup"] += pruned_count

                        tool_stats[tool]["true"] += 1
                        tool_stats[tool]["true_dedup"] += pruned_count

                    else:
                        bench_stats[(tool, bench)]["false"] += 1
                        bench_stats[(tool, bench)]["false_dedup"] += pruned_count

                        tool_stats[tool]["false"] += 1
                        tool_stats[tool]["false_dedup"] += pruned_count

    print("\n=== Per Tool / Benchmark ===")

    for tool in TOOLS:
        for bench in BENCHS:

            s = bench_stats[(tool, bench)]

            display_not_run = (
                s["not_run"]
                - s["true_dedup"]
                - s["false_dedup"]
            )

            total = (
                s["true"]
                + s["false"]
                + display_not_run
                + s["false_dedup"]
                + s["true_dedup"]
            )

            print(
                f"{tool:5s} {bench:7s}\t "
                f"True={s['true']:4d}\t "
                f"False={s['false']:4d}\t "
                f"True_dedup={s['true_dedup']:6d}\t "
                f"False_dedup={s['false_dedup']:6d}\t "
                f"Not_run={display_not_run:4d}\t "
                f"Total={total:4d}\t"
                f"all_edges={s['all_edges']:4d}"
            )

    print("\n=== Per Tool Overall ===")

    for tool in TOOLS:

        s = tool_stats[tool]

        display_not_run = (
            s["not_run"]
            - s["true_dedup"]
            - s["false_dedup"]
        )

        total = (
            s["true"]
            + s["false"]
            + display_not_run
            + s["false_dedup"]
            + s["true_dedup"]
        )

        print(
            f"{tool:5s} "
            f"True={s['true']:4d}\t "
            f"False={s['false']:4d}\t "
            f"True_dedup={s['true_dedup']:6d}\t "
            f"False_dedup={s['false_dedup']:6d}\t "
            f"Not_run={display_not_run:4d}\t "
            f"Total={total:4d}\t"
            f"all_edges={s['all_edges']:4d}"
        )


if __name__ == '__main__':
    main()