"""
pipeline/utils.py
=================
Shared helpers used across stages.
No pipeline logic lives here — only pure utilities.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from pipeline.state import PipelineState
from config import OUT_PIPELINE_RESULT, MAX_AGENT_RETRIES, AGENT_RETRY_BASE_DELAY, MAX_CONCURRENT_AGENTS

# ── Agent concurrency limiter ─────────────────────────────────────────────────

# Lazily initialised inside the event loop — caps concurrent SDK sessions
# independently of the pipeline-level semaphore.
_agent_sem: Optional[asyncio.Semaphore] = None


def _get_agent_sem() -> asyncio.Semaphore:
    global _agent_sem
    if _agent_sem is None:
        _agent_sem = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_sem


# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline")


# ── Agent runner ──────────────────────────────────────────────────────────────

async def run_agent(prompt: str, options: ClaudeAgentOptions, stage_name: str) -> str:
    """
    Run a single agent query and collect the full text response.

    Retries up to MAX_AGENT_RETRIES times on transient SDK initialization
    timeouts (e.g. "Control request timeout: initialize") that occur under
    high concurrency, using exponential backoff.

    The SDK yields three message types:
      - SystemMessage  : internal SDK housekeeping — no content, skip it
      - AssistantMessage: Claude's streamed response blocks — collect text blocks
      - ResultMessage  : final result with metadata — prefer result.result if
                         present, otherwise already captured from AssistantMessage
    """
    log.info(f"[{stage_name}] Starting...")

    for attempt in range(MAX_AGENT_RETRIES):
        collected: list[str] = []
        final_result: Optional[str] = None

        try:
            async with _get_agent_sem():
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, SystemMessage):
                        continue
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "type") and block.type == "text":
                                collected.append(block.text)
                    elif isinstance(message, ResultMessage):
                        if message.result:
                            final_result = message.result
                        log.debug(
                            f"[{stage_name}] Result metadata — "
                            f"stop_reason={message.stop_reason}, "
                            f"turns={message.num_turns}"
                        )

            result = final_result or "\n".join(collected)
            log.info(f"[{stage_name}] Done. Response: {len(result)} chars")
            return result

        except Exception as e:
            err_str = str(e)
            is_retriable = (
                "Control request timeout" in err_str
                or "TimeoutError" in type(e).__name__
                or "Command failed" in err_str
                or "exit code" in err_str
            )
            if is_retriable and attempt < MAX_AGENT_RETRIES - 1:
                delay = AGENT_RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    f"[{stage_name}] Retriable agent error (attempt {attempt + 1}/{MAX_AGENT_RETRIES}), "
                    f"retrying in {delay}s: {err_str}"
                )
                await asyncio.sleep(delay)
            else:
                raise

    return ""  # unreachable, satisfies type checker


# ── JSON extraction ───────────────────────────────────────────────────────────

def parse_json_from_response(response: str) -> Optional[dict]:
    """
    Extract a JSON object from an agent response.
    Tries direct parse first, then searches for the first {...} block.
    Returns None if no valid JSON is found — callers must handle this.
    """
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    start = response.find("{")
    end   = response.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

    log.warning("Could not parse JSON from agent response.")
    return None


# ── Final report ──────────────────────────────────────────────────────────────

def write_final_report(state: PipelineState, success: bool, reason: str) -> None:
    """
    Write pipeline_result.json to the output directory.
    Always called at the end of the pipeline, whether success or failure.
    """
    report = {
        "timestamp":  datetime.now().isoformat(),
        "success":    success,
        "loops_used": state.loop_count,
        "reason":     reason,
        "identity": {
            "program_name":    state.program_name,
            "tool_name":       state.tool_name,
            "algorithm":       state.algorithm,
            "boundary_index":  state.boundary_index,
            "boundary_id":     state.boundary_id,
        },
        "artifacts": {
            "unsoundness_report":        state.unsoundness_report,
            "reproducing_test_case":     state.reproducing_test_case_path,
            "non_reproducing_test_case": state.non_reproducing_test_case_path,
            "staticcg":                  state.staticcg_path,
            "dyncg":                     state.dyncg_path,
            "boundary":                  state.boundary_path,
            "annotated_sa_tool":         state.annotated_sa_tool_path,
            "modified_analysis":         state.modified_analysis_path,
        },
        "feedback_history": {
            "reproduce_feedback": state.reproduce_feedback,
            "trace_feedback":     state.trace_feedback,
        },
    }

    out_path = state.output_dir / OUT_PIPELINE_RESULT
    out_path.write_text(json.dumps(report, indent=2))
    log.info(f"{state.label()} Result → {out_path}")


# ── Summary printers ──────────────────────────────────────────────────────────

def print_success_summary(state: PipelineState) -> None:
    from config import MAX_LOOPS
    log.info(f"\n{state.label()} {'=' * 50}")
    log.info(f"{state.label()} SUCCESS  {state.boundary_id}")
    log.info(f"{state.label()} {'=' * 50}")
    log.info(f"{state.label()} Loops used     : {state.loop_count}/{MAX_LOOPS}")
    log.info(f"{state.label()} Report         : {state.unsoundness_report}")
    log.info(f"{state.label()} Reproducing TC : {state.reproducing_test_case_path}")
    log.info(f"{state.label()} Non-repro TC   : {state.non_reproducing_test_case_path}")
    log.info(f"{state.label()} Annotated tool : {state.annotated_sa_tool_path}")
    log.info(f"{state.label()} Modified output: {state.modified_analysis_path}")


def print_failure_summary(state: PipelineState) -> None:
    from config import MAX_LOOPS
    log.warning(f"\n{state.label()} {'=' * 50}")
    log.warning(f"{state.label()} FAILED  {state.boundary_id}")
    log.warning(f"{state.label()} {'=' * 50}")
    log.warning(f"{state.label()} Loops used : {state.loop_count}/{MAX_LOOPS}")
    for i, fb in enumerate(state.reproduce_feedback):
        log.warning(f"{state.label()} Reproduce [{i+1}]: {fb}")
    for i, fb in enumerate(state.trace_feedback):
        log.warning(f"{state.label()} Trace     [{i+1}]: {fb}")