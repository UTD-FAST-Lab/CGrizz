
import os
import json
import numpy as np
# import matplotlib.pyplot as plt
from pathlib import Path



OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
    
def get_programs_from_config(tool, bench, config):
    conf_dir = Path("/home/mohammad/projects/AgenticCGEval/ignored_stuff/configs")
    conf_file = conf_dir / f"pipeline_config_{bench}_{tool}_{config}.json"
    if not conf_file.exists():
        return set()
    
    data = read_json(conf_file)
    if data is None:
        return set()
    
    analyses = data.get("analyses", [])
    programs = set()
    for analysis in analyses:
        program = analysis.get("program_name")
        if program:
            programs.add(program)

    return programs


def get_programs(tool, bench):
    results_dir = OUTPUT_PATH / f"results_{tool}_{bench}"
    if not results_dir.exists():
        return set()
    
    programs = set()
    for program_dir in results_dir.iterdir():
        if program_dir.is_dir():
            programs.add(program_dir.name)
    
    return programs


def get_intersection_programs(bench):
    # what programs have boundaries after the filter for all 3 tools
    
    tool_programs = {tool: get_programs(tool, bench) for tool in ['wala', 'opal', 'soot']}
    print(f"Programs for WALA: {len(tool_programs['wala'])}")
    print(f"Programs for OPAL: {len(tool_programs['opal'])}")
    print(f"Programs for Soot: {len(tool_programs['soot'])}")
    intersection = set.intersection(*tool_programs.values())
    return intersection


def get_programs_before_filter(bench):
    # what programs do we have for all 3 tools 

    wala_programs = get_programs_from_config('WALA', bench, '0-CFA')
    opal_programs = get_programs_from_config('OPAL', bench, '0-CFA')
    soot_programs = get_programs_from_config('Soot', bench, 'SPARK')

    intersection = wala_programs.intersection(opal_programs).intersection(soot_programs)
    return intersection


def get_boundaries(tool, bench, program):
    results_dir = OUTPUT_PATH / f"results_{tool}_{bench}" / program
    if not results_dir.exists():
        return 0
    
    edge_dirs = [
        p for p in results_dir.glob("*/*/*")
        if p.is_dir()
    ]

    return len(edge_dirs)
    


# sort them by the sum of boundaries in each tool and create a chart showing the sum of boundaries of each tool for each program
def sort_programs_by_boundaries(bench):
    programs = get_intersection_programs(bench)
    
    count = {}
    for program in programs:
        wala_boundaries = get_boundaries('wala', bench, program)
        opal_boundaries = get_boundaries('opal', bench, program)
        soot_boundaries = get_boundaries('soot', bench, program)

        total_boundaries = wala_boundaries + opal_boundaries + soot_boundaries
        # print(f"{program}: {total_boundaries} boundaries (WALA: {wala_boundaries}, OPAL: {opal_boundaries}, Soot: {soot_boundaries})")

        count[program] = total_boundaries
    
    sorted_programs = sorted(count.items(), key=lambda x: x[1], reverse=True)
    return sorted_programs


def rewrite_configs_with_sorted_programs(bench, sorted_programs):

    # rewrite the config files with the sorted programs
    for tool in ['WALA', 'OPAL', 'Soot']:
        config_name = '0-CFA' if tool != 'Soot' else 'SPARK'
        conf_dir = Path("/home/mohammad/projects/AgenticCGEval/ignored_stuff/configs")
        conf_file = conf_dir / f"pipeline_config_{bench}_{tool}_{config_name}.json"
        
        if not conf_file.exists():
            continue
        
        data = read_json(conf_file)
        if data is None:
            continue
        
        analyses = data.get("analyses", [])
        program_order = [program for program, _ in sorted_programs]
        
        # Sort analyses based on the order of programs in program_order and remove any analyses that are not in the sorted list
        analyses.sort(key=lambda x: program_order.index(x["program_name"]) if x["program_name"] in program_order else float('inf'))        
        
        data["analyses"] = analyses
        
        # Write to a new file to avoid overwriting the original config
        new_conf_file = conf_dir / "selected_programs" / f"pipeline_config_{bench}_{tool}_{config_name}_sorted.json"
        new_conf_file.write_text(json.dumps(data, indent=4))


def main():
    bench = 'njr'
    programs_before_filter = get_programs_before_filter(bench)
    programs_after_filter = get_intersection_programs(bench)

    print(f"Programs before filter for {bench}: {len(programs_before_filter)}")
    print(f"Programs after filter for {bench}: {len(programs_after_filter)}")

    print(programs_after_filter)

    # print("\nSorted programs by total boundaries:")
    # sorted_programs = sort_programs_by_boundaries(bench)
    # for program, total_boundaries in sorted_programs:
    #     print(f"{program}: {total_boundaries} boundaries")

    # rewrite_configs_with_sorted_programs(bench, sorted_programs)


if __name__ == "__main__":
    main()