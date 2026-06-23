from pathlib import Path
import json
from collections import defaultdict

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')
TOOLS = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

GROUPS = ['agent', 'oracle', 'boundary', 'trace']

FAILURE_PATTERNS = {
    'agent': [
        'Report generation failed.',
        'Test case generation failed.',
        'Oracle generation failed',
        'Annotation failed.',
    ],

    'oracle': [
        'Agent JAR not found — skipping oracle check on real program',
        'Oracle run crashed on real program (non-zero exit code)',
        'The instrumentation was run against the real target program but the boundary edge',
        'Oracle on real program produced no symptoms'
    ],

    'boundary': [
        'Could not read oracle',
        'Oracle does not contain any root causes',
        'Oracle root causes do not contain any symptoms',
        'Edge not found for symptom',
        'Symptom in oracle missing caller or callee information.',
    ],

    'trace': [
        'Could not read execution trace log',
        'Trace check failed — no lines with pattern [UNSOUNDNESS] found in execution trace.',
        'Trace check failed — expected root cause method',
        'The method is not reachable for root cause',
        'Could not read oracle for root cause methods',
    ],
}

def group_total(d):
    return sum(d.get(g, 0) for g in GROUPS)

#
# Sort patterns by length so that more specific messages
# are matched before more general ones.
#
ALL_PATTERNS = []

for group, patterns in FAILURE_PATTERNS.items():
    for pattern in patterns:
        ALL_PATTERNS.append((pattern.lower(), group))

ALL_PATTERNS.sort(key=lambda x: len(x[0]), reverse=True)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def classify_line(line):
    """
    Classify a line into a failure group based on
    known pipeline diagnostic messages.
    """
    lower = line.lower()

    for pattern, group in ALL_PATTERNS:
        if pattern in lower:
            return group

    return None


def main():

    #
    # Summary statistics.
    #
    bench_stats = defaultdict(
        lambda: {group: 0 for group in GROUPS}
    )

    tool_stats = defaultdict(
        lambda: {group: 0 for group in GROUPS}
    )

    bench_final = defaultdict(
        lambda: {group: 0 for group in GROUPS}
    )

    tool_final = defaultdict(
        lambda: {group: 0 for group in GROUPS}
    )

    #
    # Detailed records.
    #
    any_occurrence = defaultdict(list)
    final_causes = defaultdict(list)

    for tool in TOOLS:

        for bench in BENCHS:

            results_dir = OUTPUT_PATH / f'results_{tool}_{bench}'

            if not results_dir.exists():
                continue

            for program_dir in results_dir.iterdir():

                if not program_dir.is_dir():
                    continue

                #
                # program/*/*/*
                #
                edge_dirs = [
                    p for p in program_dir.glob('*/*/*')
                    if p.is_dir()
                ]

                for edge_dir in edge_dirs:

                    pipeline_file = edge_dir / 'pipeline_result.json'

                    #
                    # Only analyze failed edges.
                    #
                    if not pipeline_file.exists():
                        continue

                    pipeline_data = read_json(pipeline_file)

                    if pipeline_data is None:
                        continue

                    if pipeline_data.get('success', False):
                        continue

                    log_file = edge_dir / 'pipeline.log'

                    if not log_file.exists():
                        continue

                    groups_seen = set()
                    last_group = None

                    try:
                        with open(log_file, 'r', errors='ignore') as f:

                            for line in f:

                                #
                                # The last matched group before this
                                # separator is considered the final cause.
                                #
                                if 'Loops used' in line:
                                    break

                                group = classify_line(line)

                                if group is not None:
                                    groups_seen.add(group)
                                    last_group = group

                    except Exception:
                        continue

                    edge_path = str(edge_dir.relative_to(results_dir))

                    #
                    # Record any occurrence.
                    #
                    for group in groups_seen:

                        bench_stats[(tool, bench)][group] += 1
                        tool_stats[tool][group] += 1

                        record = {
                            'program': program_dir.name,
                            'edge': edge_dir.name,
                            'path': edge_path,
                            'group': group,
                        }

                        any_occurrence[f'{tool}/{bench}'].append(record)
                        any_occurrence[tool].append(record)

                    #
                    # Record final cause.
                    #
                    if last_group is not None:

                        bench_final[(tool, bench)][last_group] += 1
                        tool_final[tool][last_group] += 1

                        record = {
                            'program': program_dir.name,
                            'edge': edge_dir.name,
                            'path': edge_path,
                            'final_group': last_group,
                        }

                        final_causes[f'{tool}/{bench}'].append(record)
                        final_causes[tool].append(record)

    #
    # Convert defaultdicts to regular dicts.
    #
    any_occurrence = dict(any_occurrence)
    final_causes = dict(final_causes)

    #
    # Save outputs.
    #
    output_dir = OUTPUT_PATH / 'failure_analysis'
    output_dir.mkdir(exist_ok=True)

    with open(output_dir / 'any_occurrence_details.json', 'w') as f:
        json.dump(any_occurrence, f, indent=2)

    with open(output_dir / 'final_cause_details.json', 'w') as f:
        json.dump(final_causes, f, indent=2)

    with open(output_dir / 'any_occurrence_summary.json', 'w') as f:
        json.dump(
            {
                'per_tool_benchmark': {
                    f'{tool}/{bench}': {
                        **bench_stats[(tool, bench)],
                        "total": group_total(bench_stats[(tool, bench)])
                    }
                    for tool in TOOLS
                    for bench in BENCHS
                },

                'per_tool': {
                    tool: {
                        **tool_stats[tool],
                        "total": group_total(tool_stats[tool])
                    }
                    for tool in TOOLS
                },
            },
            f,
            indent=2,
        )

    with open(output_dir / 'final_cause_summary.json', 'w') as f:
        json.dump(
            {
                'per_tool_benchmark': {
                    f'{tool}/{bench}': {
                        **bench_final[(tool, bench)],
                        "total": group_total(bench_final[(tool, bench)])
                    }
                    for tool in TOOLS
                    for bench in BENCHS
                },

                'per_tool': {
                    tool: {
                        **tool_final[tool],
                        "total": group_total(tool_final[tool])
                    }
                    for tool in TOOLS
                },
            },
            f,
            indent=2,
        )

    print(f'Results written to: {output_dir}')


if __name__ == '__main__':
    main()