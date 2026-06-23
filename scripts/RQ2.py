import numpy as np
from sklearn.model_selection import KFold
from collections import defaultdict
from pathlib import Path
import json

TOOLS = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']
OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def generate_groups():
    # return [
    #         # Group 0: Exclusively pA
    #         [('pA', 'e_init'), ('pA', 'e_load')],
            
    #         # Group 1: Shared across pA and pB (e.g., a common library utility)
    #         [('pA', 'e_util'), ('pB', 'e_util')],
            
    #         # Group 2: Exclusively pC (large group)
    #         [('pC', 'e_proc1'), ('pC', 'e_proc2'), ('pC', 'e_proc3')],
            
    #         # Group 3: Shared across pB, pC, and pD (e.g., framework boilerplate)
    #         [('pB', 'e_fw'), ('pC', 'e_fw'), ('pD', 'e_fw')],
            
    #         # Group 4: pE specific
    #         [('pE', 'e_async1'), ('pE', 'e_async2')],
            
    #     ]

    for tool in TOOLS:

        if tool != "soot":
            continue
        
        groups = []

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

                for edge_dir in edge_dirs:

                    group = set()

                    pipeline_file = edge_dir / "pipeline_result.json"
                    pruning_file = edge_dir / "pruning_report.json"

                    program = program_dir.name
                    edge = str(edge_dir.relative_to(results_dir))

    
                    if not pipeline_file.exists():
                        continue

                    pipeline_data = read_json(pipeline_file)

                    if pipeline_data is None:
                        continue

                    success = pipeline_data.get("success", False)

                    if not success:
                        continue

                    if pruning_file.exists():
                        pruning_data = read_json(pruning_file)
                        if pruning_data is not None:
                            triggered_by = pruning_data.get("triggered_by", {})
                            edge_id = triggered_by.get("boundary_id", None)
                            program_name = triggered_by.get("program_name", None)

                            if edge_id is not None and program_name is not None:
                                # print("Adding to group from pruning report:", program_name, edge_id)
                                group.add((program_name, edge_id))

                            for boundary in pruning_data.get("pruned_boundaries", []):
                                edge_id = boundary.get("boundary_id", None)
                                program_name = boundary.get("program_name", None)

                                if edge_id is not None and program_name is not None:
                                    # print("Adding to group from pruning report:", program_name, edge_id)
                                    group.add((program_name, edge_id))

                    else:
                        bid = pipeline_data.get("identity", {})
                        edge_id = bid.get("boundary_id", None)
                        program_name = bid.get("program_name", None)
                        if edge_id is not None and program_name is not None:
                            group.add((program_name, edge_id))

                    groups.append(group)

        return groups


def calculate_group_based_recall(groups):
    # groups structure as provided: 
    # Each sublist contains (program_name, edge_id)
    # Example: groups = [[('pA', 'e1'), ('pA', 'e2')], [('pB', 'e3')]]


    # Map program -> set of group_indices it belongs to
    program_to_groups = defaultdict(set)
    for i, group in enumerate(groups):
        for program, edge_id in group:
            program_to_groups[program].add(i)

    # Get unique list of all programs involved
    programs = list(program_to_groups.keys())
    programs_arr = np.array(programs)

    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    recalls = []
    # 2. Iterate through folds
    for fold, (train_idx, test_idx) in enumerate(kf.split(programs_arr)):
        train_progs = set(programs_arr[train_idx])
        test_progs = programs_arr[test_idx]
        
        # Identify which groups are "seen" in training
        # A group is seen if at least one (program, edge_id) in it belongs to a train_prog
        seen_groups = set()
        for i, group in enumerate(groups):
            for program, edge_id in group:
                if program in train_progs:
                    seen_groups.add(i)
                    break
        
        total_test_edges_in_test_progs = 0
        total_group_matched_edges = 0
        
        # 3. Calculate metrics
        for prog in test_progs:
            program_total_edges = sum(1 for group in groups for p, e in group if p == prog)
            total_test_edges_in_test_progs += program_total_edges
            
            # Check for matches
            for i in program_to_groups[prog]:
                if i in seen_groups:
                    # Add all edges of this group that belong to the current test program
                    matched_in_group = sum(1 for p, e in groups[i] if p == prog)
                    total_group_matched_edges += matched_in_group
                    
        recall = total_group_matched_edges / total_test_edges_in_test_progs if total_test_edges_in_test_progs > 0 else 0
        print("test_progs:", test_progs, "training_progs:", list(train_progs))
        print(f"Fold {fold+1} Group-Based Recall: {recall:.4f}")
        recalls.append(recall)

    print("Overall Group-Based Recall:", np.mean(recalls))


def main():
    groups = generate_groups()
    # print(groups)
    calculate_group_based_recall(groups)


if __name__ == "__main__":
    main()