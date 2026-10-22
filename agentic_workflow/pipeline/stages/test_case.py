"""
pipeline/stages/test_case.py
=============================
Stage 4: the reproducing and non-reproducing (near miss) test cases, kept small
(issue #3): a size budget right after generation, and stage 6b, which shrinks
them once the reproduction check passed and keeps the smaller version only if
every check still passes.
"""

import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.timing import timed
from pipeline.test_case_size import measure
from pipeline.agents import build_agent_options
from pipeline.agent_runner import run_agent, parse_json_from_response
from config import (
    OUT_TEST_CASE_DIR,
    OUT_STATICCG_DIR,
    OUT_DYNCG_DIR,
    OUT_BOUNDARY_DIR,
    TEST_CASE_MAX_FILES,
    TEST_CASE_MAX_LINES,
    TEST_CASE_SIMPLIFY_ATTEMPTS,
    TEST_CASE_SHRINK_ABOVE_LINES,
    TEST_CASE_SHRINK_ATTEMPTS,
)

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
            await _enforce_budget(state)
            return True

    log.error(f"{state.label()} Test case generation failed.")
    return False


# ── Size budget and shrinking (issue #3) ──────────────────────────────────────


async def _enforce_budget(state: PipelineState) -> None:
    """Over budget → ask for a simpler test case (TEST_CASE_SIMPLIFY_ATTEMPTS). Not fatal."""
    size = measure(state.reproducing_test_case_path)
    state.test_case_size = size.as_dict()
    for _ in range(TEST_CASE_SIMPLIFY_ATTEMPTS):
        if not size.over(TEST_CASE_MAX_FILES, TEST_CASE_MAX_LINES):
            return
        log.info(f"{state.label()} Test case over budget ({size}); asking for a simpler one.")
        await run_agent(
            prompt=_resize_prompt(state, size, f"is over the size budget (at most {TEST_CASE_MAX_LINES} lines of "
                                               f"code in {TEST_CASE_MAX_FILES} Java files)"),
            options=build_agent_options(state),
            stage_name=f"{state.label()} SimplifyTestCase",
        )
        before, size = size, measure(state.reproducing_test_case_path)
        state.test_case_size = {**size.as_dict(), "budget_simplified_from": before.lines}
    if size.over(TEST_CASE_MAX_FILES, TEST_CASE_MAX_LINES):
        log.warning(f"{state.label()} Test case still over budget ({size}); continuing with it.")


def _resize_prompt(state: PipelineState, size, problem: str) -> str:
    near_miss = (f"\nNon-reproducing (near miss): {state.non_reproducing_test_case_path}/ — make the same changes "
                 "there, so the two still differ only in the condition the tool depends on.\n"
                 if state.non_reproducing_test_case_path and Path(state.non_reproducing_test_case_path).exists() else "")
    return f"""\
Use the test-case-generator agent to make the existing test case smaller. Invoke it via the Task tool.

The reproducing test case {problem}: it has {size}.

Unsoundness report : {state.unsoundness_report}
Reproducing        : {state.reproducing_test_case_path}/
{near_miss}
Edit the files in place. Delete every class, method, field and statement the root cause does not need:
keep only what makes the tool miss an edge for the reason in the report's toolLimitation. Keep the
package, the main class, pom.xml and jcg.conf working (`mvn package` must still build the jar).
Do not copy code from the target program. Write only inside the directories above.

Return JSON only: {{"reproducing": true}}"""


def _snapshot(state: PipelineState) -> dict[Path, Path]:
    """Copy the test cases aside; restore with _restore."""
    saved = {}
    for d in (state.reproducing_test_case_path, state.non_reproducing_test_case_path):
        if d and Path(d).exists():
            backup = Path(d).with_name(f".{Path(d).name}.before_shrink")
            shutil.rmtree(backup, ignore_errors=True)
            shutil.copytree(d, backup)
            saved[Path(d)] = backup
    return saved


def _restore(saved: dict[Path, Path]) -> None:
    for d, backup in saved.items():
        shutil.rmtree(d, ignore_errors=True)
        shutil.move(str(backup), str(d))


def _discard(saved: dict[Path, Path]) -> None:
    for backup in saved.values():
        shutil.rmtree(backup, ignore_errors=True)


def _clear_outputs(state: PipelineState) -> None:
    """Remove build and analysis outputs, so the checks rebuild and re-analyse the changed sources."""
    for d in (state.reproducing_test_case_path, state.non_reproducing_test_case_path):
        if not d or not Path(d).exists():
            continue
        for name in ("target", OUT_STATICCG_DIR, OUT_DYNCG_DIR, OUT_BOUNDARY_DIR):
            shutil.rmtree(Path(d) / name, ignore_errors=True)


@timed("test_case_shrink")
async def stage_shrink_test_case(state: PipelineState) -> None:
    """
    After the reproduction check passed: try to make the test cases smaller.
    A smaller version is kept only if the oracle checks (symptoms on the
    reproducing test case, none on the near miss) and the reproduction check
    still pass; otherwise both test cases are restored. Never fails the boundary.
    """
    from pipeline.stages.oracle import revalidate_oracle
    from pipeline.stages.reproduction import stage_check_reproduction

    size = measure(state.reproducing_test_case_path)
    state.test_case_size = {**state.test_case_size, **size.as_dict()}
    for attempt in range(1, TEST_CASE_SHRINK_ATTEMPTS + 1):
        if size.lines <= TEST_CASE_SHRINK_ABOVE_LINES:
            return
        log.info(f"{state.label()} Shrinking the test case ({size}), attempt {attempt}/{TEST_CASE_SHRINK_ATTEMPTS}")
        saved = _snapshot(state)
        await run_agent(
            prompt=_resize_prompt(state, size, "reproduces the missed edge; now make it as small as possible"),
            options=build_agent_options(state),
            stage_name=f"{state.label()} ShrinkTestCase",
        )
        smaller = measure(state.reproducing_test_case_path)
        if smaller.lines >= size.lines:
            log.info(f"{state.label()} Shrink attempt did not make the test case smaller ({smaller}); keeping it.")
            _restore(saved)
            return
        _clear_outputs(state)
        problem = await revalidate_oracle(state)
        reproduced, feedback = (False, problem) if problem else await stage_check_reproduction(state)
        if not reproduced:
            log.info(f"{state.label()} Smaller test case rejected, restoring the previous one: "
                     f"{feedback.splitlines()[0] if feedback else ''}")
            _restore(saved)
            return
        _discard(saved)
        log.info(f"{state.label()} Test case shrunk from {size.lines} to {smaller.lines} lines.")
        state.test_case_size = {**state.test_case_size, **smaller.as_dict(), "shrunk_from": size.lines}
        size = smaller
