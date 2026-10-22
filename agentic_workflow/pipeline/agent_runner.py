"""
pipeline/agent_runner.py
========================
run_agent() — runs one Claude Agent SDK session with retries and a global
concurrency cap — and JSON extraction from agent responses.
"""

import asyncio
import json
import logging
from typing import Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

import pipeline.logs  # noqa: F401  (configures logging on import)
from config import MAX_AGENT_RETRIES, AGENT_RETRY_BASE_DELAY, MAX_CONCURRENT_AGENTS

# ── Agent concurrency limiter ─────────────────────────────────────────────────

# Lazily initialised inside the event loop — caps concurrent SDK sessions
# independently of the pipeline-level semaphore.
_agent_sem: Optional[asyncio.Semaphore] = None

log = logging.getLogger("pipeline")


def _get_agent_sem() -> asyncio.Semaphore:
    global _agent_sem
    if _agent_sem is None:
        _agent_sem = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_sem

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
                or "error result" in err_str   # SDK wraps CLI non-zero exit as "error result: <subtype>"
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
