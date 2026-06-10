from pathlib import Path
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


def classify_line(line):
    """
    Classify a warning/error line into one of the failure groups.

    Matching is case-insensitive.
    """
    lower = line.lower()

    if "[warning]" not in lower and "[error]" not in lower:
        return None

    if "boundary" in lower:
        return "boundary"

    if "oracle" in lower:
        return "oracle"

    if "trace" in lower:
        return "trace"

    return None


def main():

    #
    # Counts edges that had at least one warning/error
    # belonging to each category.
    #
    bench_stats = defaultdict(
        lambda: {"oracle": 0, "boundary": 0, "trace": 0}
    )

    tool_stats = defaultdict(
        lambda: {"oracle": 0, "boundary": 0, "trace": 0}
    )

    #
    # Counts the LAST warning/error category before the
    # separator line for each failed edge.
    #
    bench_final = defaultdict(
        lambda: {"oracle": 0, "boundary": 0, "trace": 0}
    )

    tool_final = defaultdict(
        lambda: {"oracle": 0, "boundary": 0, "trace": 0}
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

                for edge_dir in edge_dirs:

                    pipeline_file = edge_dir / "pipeline_result.json"

                    #
                    # Only analyze failed edges.
                    #
                    if not pipeline_file.exists():
                        continue

                    pipeline_data = read_json(pipeline_file)

                    if pipeline_data is None:
                        continue

                    if pipeline_data.get("success", False):
                        continue

                    log_file = edge_dir / "pipeline.log"

                    if not log_file.exists():
                        continue

                    groups_seen = set()
                    last_group = None

                    try:
                        with open(log_file, "r", errors="ignore") as f:

                            for line in f:

                                #
                                # The last warning/error group before
                                # this separator is considered the
                                # final cause.
                                #
                                if "=======================" in line:
                                    break

                                group = classify_line(line)

                                if group is not None:
                                    groups_seen.add(group)
                                    last_group = group

                    except Exception:
                        continue

                    #
                    # Count each group at most once per edge.
                    #
                    for group in groups_seen:
                        bench_stats[(tool, bench)][group] += 1
                        tool_stats[tool][group] += 1

                    #
                    # Count the final group.
                    #
                    if last_group is not None:
                        bench_final[(tool, bench)][last_group] += 1
                        tool_final[tool][last_group] += 1

    print("\n=== Per Tool / Benchmark (Any Occurrence) ===")

    for tool in TOOLS:
        for bench in BENCHS:

            s = bench_stats[(tool, bench)]

            total = (
                s["oracle"]
                + s["boundary"]
                + s["trace"]
            )

            print(
                f"{tool:5s} {bench:7s}\t"
                f"oracle={s['oracle']:4d}\t"
                f"trace={s['trace']:4d}\t"
                f"boundary={s['boundary']:4d}\t"
                f"Total={total:4d}"
            )

    print("\n=== Per Tool Overall (Any Occurrence) ===")

    for tool in TOOLS:

        s = tool_stats[tool]

        total = (
            s["oracle"]
            + s["boundary"]
            + s["trace"]
        )

        print(
            f"{tool:5s}\t"
            f"oracle={s['oracle']:4d}\t"
            f"trace={s['trace']:4d}\t"
            f"boundary={s['boundary']:4d}\t"
            f"Total={total:4d}"
        )

    print("\n=== Per Tool / Benchmark (Final Cause) ===")

    for tool in TOOLS:
        for bench in BENCHS:

            s = bench_final[(tool, bench)]

            total = (
                s["oracle"]
                + s["boundary"]
                + s["trace"]
            )

            print(
                f"{tool:5s} {bench:7s}\t"
                f"oracle={s['oracle']:4d}\t"
                f"trace={s['trace']:4d}\t"
                f"boundary={s['boundary']:4d}\t"
                f"Total={total:4d}"
            )

    print("\n=== Per Tool Overall (Final Cause) ===")

    for tool in TOOLS:

        s = tool_final[tool]

        total = (
            s["oracle"]
            + s["boundary"]
            + s["trace"]
        )

        print(
            f"{tool:5s}\t"
            f"oracle={s['oracle']:4d}\t"
            f"trace={s['trace']:4d}\t"
            f"boundary={s['boundary']:4d}\t"
            f"Total={total:4d}"
        )


if __name__ == '__main__':
    main()