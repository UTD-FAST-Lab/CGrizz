"""
pipeline/agents.py
==================
Single factory function that builds ClaudeAgentOptions.

Because setting_sources=["user", "project"] is set, the SDK automatically
discovers your existing .claude/agents/*.md subagent definitions —
no AgentDefinition blocks needed here.

To override a specific subagent's model without touching the .md file,
uncomment the AgentDefinition block below and add it to the agents= dict.
"""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
# from claude_agent_sdk import AgentDefinition  # uncomment if overriding .md agents

from config import MODEL_DEFAULT
from pipeline.state import PipelineState


def build_agent_options(state: PipelineState) -> ClaudeAgentOptions:
    """
    Returns a ClaudeAgentOptions instance for use in every stage.

    setting_sources=["user", "project"] loads:
      - ~/.claude/agents/       (user-level agents)
      - .claude/agents/         (project-level agents — your existing .md files)
      - .claude/settings.json   (hooks)
      - CLAUDE.md               (project memory)

    cwd must point to the directory that contains .claude/
    (i.e. your project root, which is one level above this package).
    """
    project_root = Path(__file__).parent.parent

    return ClaudeAgentOptions(
        model=MODEL_DEFAULT,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
        setting_sources=["user", "project"],
        cwd=str(project_root),

        # ── Optional: override individual agent models ─────────────────────
        # Uncomment to use a different model for a specific subagent without
        # editing its .md file. The SDK definition takes priority over the file.
        #
        # agents={
        #     "boundary-selector": AgentDefinition(
        #         model=MODEL_FAST,
        #         description="Selects relevant boundary edges from SCG/DCG overlap.",
        #     ),
        #     "root-cause-annotator": AgentDefinition(
        #         model=MODEL_POWERFUL,
        #         description="Annotates the SA tool to trace unsoundness root cause.",
        #     ),
        # },
    )