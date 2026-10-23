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
from pipeline.edges import (boundary_edges, edge_in_boundary, edge_in_cg, is_reachable, method_reachable,
                            reflective_edge_in_boundary)

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
    return await recheck_reproduction(state)


async def recheck_reproduction(state: PipelineState) -> tuple[bool, str]:
    """The reproduction check without re-running the tool: after the spec changed, on the same test case."""
    ok = await generate_oracle_testcase(state)  # generates oracle.json in the reproducing test case dir
    if not ok:
        return False, ("[reproduction check] The oracle could not run on the rebuilt reproducing test case: it must "
                       "build with `mvn package` and the main class in its jcg.conf must run to completion.")
    return check_symptoms(state)


def check_symptoms(state: PipelineState) -> tuple[bool, str]:
    """
    Every symptom of the test case's oracle must be a missed edge. If some are and the tool
    resolves the others, the hypothesis reproduces but the spec names too many calls:
    state.spec_too_broad_feedback says which, for the instrumentation agent.
    """
    state.spec_too_broad_feedback = None
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

    all_symptoms = [sym for rc in rc_entries for sym in rc.get("symptoms", [])]
    if not all_symptoms:
        msg = ("[reproduction check] After the rebuild the root cause ran in the reproducing test case, but nothing it "
               "produced reached a call, so the test case has no missed edge to check.")
        log.error(f"{state.label()} {msg}")
        return False, msg

    missed, not_missed = [], []
    for symptom in all_symptoms:
        expected_caller = symptom.get("caller", "")
        expected_callee = symptom.get("callee", "")
        if not (expected_caller and expected_callee):
            msg = "Symptom in oracle missing caller or callee information."
            log.error(f"{state.label()} {msg}")
            return False, msg
        if not is_reachable(expected_caller, state.staticcg_path):
            continue
        if symptom.get("via") == "reflection":
            # The member a reflective call reaches: the JVM calls it from a JDK reflection accessor.
            # Missed if the tool cannot reach it at all (it lies behind a missed edge, e.g. the
            # reflective call itself), or if the accessor's call to it is a missed edge.
            reproduced = method_reachable(expected_callee, state.staticcg_path) is False \
                or reflective_edge_in_boundary(state.boundary_path, expected_callee)
            resolved = not reproduced
        else:
            log.info(f"{state.label()} Checking for edge in boundary: {expected_caller} → {expected_callee}")
            reproduced, _ = edge_in_boundary(state.boundary_path, expected_caller, expected_callee)
            resolved = not reproduced and edge_in_cg(state.staticcg_path, expected_caller, expected_callee) is True
        (missed if reproduced else not_missed).append((expected_caller, expected_callee, resolved))

    if not not_missed:
        log.info(f"{state.label()} Reproduction check passed — edge found in boundary.")
        return True, "Reproduction check passed — edge found in boundary."

    for caller, callee, _ in not_missed:
        log.error(f"{state.label()} Symptom not missed by the tool: {caller} → {callee}")
    if missed and all(resolved for _, _, resolved in not_missed):
        state.spec_too_broad_feedback = _spec_too_broad(state, missed, not_missed)
        return False, state.spec_too_broad_feedback
    caller, callee, _ = not_missed[0]
    return False, _not_reproduced(state, caller, callee)


def _spec_too_broad(state: PipelineState, missed: list, resolved: list) -> str:
    """The test case reproduces the miss, but the spec also names calls the tool resolves."""
    lines = [f"[reproduction check] The test case reproduces the miss, but the spec's symptoms also include calls "
             f"{state.tool_name}/{state.algorithm} resolves, so they are not caused by the root cause:"]
    lines += [f"  resolved: {c} → {e}" for c, e, _ in resolved[:5]]
    lines += [f"  missed:   {c} → {e}" for c, e, _ in missed[:5]]
    lines.append("Narrow the spec so that only the missed calls are symptoms — e.g. when the call on the tagged value "
                 "itself (such as newInstance() on a tagged Class) is resolved and only calls on the object it creates "
                 "are missed, match `receiverClass` instead of `receiver`.")
    return "\n".join(lines)


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
