"""
pipeline/stages/reproduction.py
================================
Stage 6: run the SA tool on the test case and confirm the edge is missed.
"""

import json
import logging
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.analysis import run_analysis_interface
from pipeline.edges import boundary_edges, edge_in_boundary, edge_in_cg, is_reachable

from pipeline.stages.oracle import generate_oracle_testcase

log = logging.getLogger("pipeline")


# ── Stage 6: Reproduction check ───────────────────────────────────────────────


@timed("reproduction")
async def stage_check_reproduction(state: PipelineState) -> tuple[bool, str]:
    """
    Run the CG analysis interface on the reproducing test case, then check
    whether the expected caller→callee edge from the unsoundness report
    appears in the boundary output (edges present in dyncg, absent in staticcg).

    No agent is needed — this is a direct lookup in the boundary JSON files.

    Returns:
        (reproduced: bool, feedback: str)
    """
    state.staticcg_path, state.dyncg_path, state.boundary_path = \
        run_analysis_interface(state)

    ok = await generate_oracle_testcase(state)  # generates oracle.json in the reproducing test case dir
    if not ok:
        return False, ("[reproduction check] The oracle could not run on the rebuilt reproducing test case: it must "
                       "build with `mvn package` and the main class in its jcg.conf must run to completion.")

    # Read oracle from the test case directory
    # Oracle format: {"RC:method@pc": {"method": ..., "pc": ..., "symptoms": [...]}, ...}
    try:
        oracle = json.loads(Path(state.oracle_path).read_text())
    except Exception as e:
        msg = f"Could not read oracle: {e}"
        log.error(f"{state.label()} {msg}")
        return False, msg

    # RC entries are top-level values (keys are "RC:..." strings)
    rc_entries = list(oracle.values())
    if not rc_entries:
        msg = ("[reproduction check] After the rebuild the oracle saw no root cause in the reproducing test case, "
               "so the test case no longer executes the root-cause instruction.")
        log.error(f"{state.label()} {msg}")
        return False, msg

    # check for symptoms of root causes in the oracle
    all_symptoms = []
    for rc in rc_entries:
        symptoms = rc.get("symptoms", [])
        all_symptoms.extend(symptoms)
    if not all_symptoms:
        msg = ("[reproduction check] After the rebuild the root cause ran in the reproducing test case, but nothing it "
               "produced reached a call, so the test case has no missed edge to check.")
        log.error(f"{state.label()} {msg}")
        return False, msg
    
    # Search boundary files for the symptoms
    for symptom in all_symptoms:
        expected_caller = symptom.get("caller", "")
        expected_callee = symptom.get("callee", "")
        if expected_caller and expected_callee:
            if not is_reachable(expected_caller, state.staticcg_path):
                continue
            log.info(f"{state.label()} Checking for edge in boundary: {expected_caller} → {expected_callee}")
            reproduced, feedback = edge_in_boundary(state.boundary_path, expected_caller, expected_callee)
            if not reproduced:
                log.error(f"{state.label()} Edge not found for symptom: {feedback}")
                return False, _not_reproduced(state, expected_caller, expected_callee)
        else:
            msg = "Symptom in oracle missing caller or callee information."
            log.error(f"{state.label()} {msg}")
            return False, msg
    
    log.info(f"{state.label()} Reproduction check passed — edge found in boundary.")
    return True, "Reproduction check passed — edge found in boundary."


def _not_reproduced(state: PipelineState, caller: str, callee: str) -> str:
    """Why an oracle symptom of the test case is not a missed edge, and what that implies."""
    ran = edge_in_cg(state.dyncg_path, caller, callee)
    resolved = edge_in_cg(state.staticcg_path, caller, callee)
    yes_no = {True: "yes", False: "no", None: "unknown"}
    lines = [
        f"[reproduction check] In the reproducing test case the edge {caller} → {callee} (a symptom of your root "
        f"cause) is not missed by {state.tool_name}/{state.algorithm}.",
        f"- executed at run time (dynamic call graph): {yes_no[ran]}",
        f"- resolved by the tool (static call graph): {yes_no[resolved]}",
    ]
    if ran is False:
        lines.append("Meaning: the test case never executes this call, so it cannot show the unsoundness. Make its "
                     "main method run the call path.")
    elif resolved:
        lines.append(f"Meaning: {state.tool_name} handles the test case's version of the mechanism, so the test case "
                     "does not hit the limitation. Either the hypothesis does not explain the original miss, or the "
                     "test case lost the condition under which the tool gives up (see toolLimitation.codeSnippet).")
    missed = boundary_edges(state.boundary_path)
    lines.append("Edges the tool does miss in this test case: " + ("; ".join(missed) if missed else "none"))
    return "\n".join(lines)
