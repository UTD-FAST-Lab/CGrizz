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


    def nested():
        return defaultdict(list)

    detailed = defaultdict(lambda: {
        "true": nested(),
        "false": nested(),
        "not_run": nested(),
        "true_dedup": nested(),
        "false_dedup": nested(),
    })

    for tool in TOOLS:

        for bench in BENCHS:

            results_dir = OUTPUT_PATH / f"results_{tool}_{bench}"

            if not results_dir.exists():
                continue

            for program_dir in results_dir.iterdir():

                if not program_dir.is_dir():
                    continue

                edge_dirs = [
                    p for p in program_dir.glob("*/*/*")
                    if p.is_dir()
                ]

                bench_stats[(tool, bench)]["all_edges"] += len(edge_dirs)
                tool_stats[tool]["all_edges"] += len(edge_dirs)

                for edge_dir in edge_dirs:

                    pipeline_file = edge_dir / "pipeline_result.json"
                    pruning_file = edge_dir / "pruning_report.json"

                    program = program_dir.name
                    edge = str(edge_dir.relative_to(results_dir))

                    #
                    # NOT RUN (UNCHANGED LOGIC)
                    #
                    if not pipeline_file.exists():
                        bench_stats[(tool, bench)]["not_run"] += 1
                        tool_stats[tool]["not_run"] += 1

                        detailed[f"{tool}/{bench}"]["not_run"][program].append(edge_dir.name)
                        continue

                    pipeline_data = read_json(pipeline_file)

                    if pipeline_data is None:
                        bench_stats[(tool, bench)]["not_run"] += 1
                        tool_stats[tool]["not_run"] += 1

                        detailed[f"{tool}/{bench}"]["not_run"][program].append(edge_dir.name)

                        continue

                    success = pipeline_data.get("success", False)

                    pruned_count = 0
                    if pruning_file.exists():
                        pruning_data = read_json(pruning_file)
                        if pruning_data is not None:
                            pruned_count = pruning_data.get("pruned_count", 0)

                    #
                    # TRUE (UNCHANGED LOGIC)
                    #
                    if success:
                        bench_stats[(tool, bench)]["true"] += 1
                        bench_stats[(tool, bench)]["true_dedup"] += pruned_count

                        tool_stats[tool]["true"] += 1
                        tool_stats[tool]["true_dedup"] += pruned_count

                        detailed[f"{tool}/{bench}"]["true"][program].append(edge_dir.name)

                        if pruned_count > 0:
                            detailed[f"{tool}/{bench}"]["true_dedup"][program].append({
                                "edge": edge_dir.name,
                                "pruned_count": pruned_count
                            })

                    #
                    # FALSE (UNCHANGED LOGIC)
                    #
                    else:
                        bench_stats[(tool, bench)]["false"] += 1
                        bench_stats[(tool, bench)]["false_dedup"] += pruned_count

                        tool_stats[tool]["false"] += 1
                        tool_stats[tool]["false_dedup"] += pruned_count

                        detailed[f"{tool}/{bench}"]["false"][program].append(edge_dir.name)

                        if pruned_count > 0:
                            detailed[f"{tool}/{bench}"]["false_dedup"][program].append({
                                "edge": edge_dir.name,
                                "pruned_count": pruned_count
                            })

    #
    # EXACT SAME PRINTING LOGIC (UNCHANGED)
    #
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

    #
    # WRITE ONLY EXTRA INSPECTION OUTPUT (NO IMPACT ON STATS)
    #
    # output_dir = OUTPUT_PATH / "failure_analysis"
    # output_dir.mkdir(exist_ok=True)

    # with open(output_dir / "detailed_edges.json", "w") as f:
    #     json.dump(detailed, f, indent=2)


if __name__ == '__main__':
    main()