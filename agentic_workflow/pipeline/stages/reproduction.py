"""
pipeline/stages/reproduction.py
================================
Stage 6: run the SA tool on the test case and confirm the edge is missed.
"""

import json
import logging
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.analysis import run_analysis_interface
from pipeline.edges import edge_in_boundary, is_reachable

from pipeline.stages.oracle import generate_oracle_testcase

log = logging.getLogger("pipeline")


# ── Stage 6: Reproduction check ───────────────────────────────────────────────

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
        return False, "Failed to generate oracle test case."

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
        msg = "Oracle does not contain any root causes."
        log.error(f"{state.label()} {msg}")
        return False, msg

    # check for symptoms of root causes in the oracle
    all_symptoms = []
    for rc in rc_entries:
        symptoms = rc.get("symptoms", [])
        all_symptoms.extend(symptoms)
    if not all_symptoms:
        msg = "Oracle root causes do not contain any symptoms."
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
                return False, feedback
        else:
            msg = "Symptom in oracle missing caller or callee information."
            log.error(f"{state.label()} {msg}")
            return False, msg
    
    log.info(f"{state.label()} Reproduction check passed — edge found in boundary.")
    return True, "Reproduction check passed — edge found in boundary."
