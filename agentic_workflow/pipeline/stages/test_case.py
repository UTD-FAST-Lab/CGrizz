"""
pipeline/stages/test_case.py
=============================
Stage 4: the reproducing and non-reproducing test cases.
"""

import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.agents import build_agent_options
from pipeline.agent_runner import run_agent, parse_json_from_response
from config import OUT_TEST_CASE_DIR

log = logging.getLogger("pipeline")


# ── Stage 4: Test case generation ─────────────────────────────────────────────


@timed("test_case")
async def stage_generate_test_case(state: PipelineState) -> bool:
    """
    Delegates to the test-case-generator agent to create two minimal Maven projects:
    one reproducing the unsoundness, one without the root cause.
    """
    options = build_agent_options(state)
    out_dir = state.out(OUT_TEST_CASE_DIR)

    state.reproducing_test_case_path     = out_dir + "/reproducing"
    state.non_reproducing_test_case_path = out_dir + "/non_reproducing"

    if state.loop_count > 0:
        for d in [state.reproducing_test_case_path, state.non_reproducing_test_case_path]:
            if d and Path(d).exists():
                shutil.rmtree(Path(d))
                log.info(f"{state.label()} Deleted stale test case dir: {d}")

    response = await run_agent(
        prompt=f"""\
Use the test-case-generator agent to generate test cases for this boundary.

## Unsoundness report

{state.unsoundness_report}

## Output directories

Reproducing test case     : {state.reproducing_test_case_path}/
Non-reproducing test case: {state.non_reproducing_test_case_path}/

IMPORTANT: The agent must write ALL files exclusively inside the two output directories
listed above. It must NOT create, modify, or write any file outside those directories
(not in the current working directory, not in any parent directory, nowhere else).

Return the JSON result from the agent directly.
## Return

Return JSON only:
{{
  "reproducing": "true | false (if generation failed)",
  "non_reproducing": "true | false (if generation failed or skipped)",
}}


""",
        options=options,
        stage_name=f"{state.label()} TestCaseGeneration",
    )

    result = parse_json_from_response(response)
    log.info(f"{state.label()} Test case generation result: {result}")
    if result:
        # repro = bool(result.get("reproducing", "false").lower() == "true")
        repro = bool(result.get("reproducing", False))
        # non_repro = bool(result.get("non_reproducing", "false").lower() == "true")
        non_repro = bool(result.get("non_reproducing", False))
        if not non_repro:
            log.warning(f"{state.label()} Non-reproducing test case generation failed or skipped.")
            state.non_reproducing_test_case_path = None
        if repro:
            log.info(f"{state.label()} Reproducing test case generated.")
            return True

    log.error(f"{state.label()} Test case generation failed.")
    return False
