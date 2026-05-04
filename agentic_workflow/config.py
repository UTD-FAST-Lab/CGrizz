"""
config.py
=========
All pipeline constants. This is the only file you need to edit
for tuning behaviour — no logic lives here.
"""

# ── Loop control ───────────────────────────────────────────────────────────────

MAX_LOOPS              = 2    # Maximum retries before declaring failure
MAX_AGENT_RETRIES      = 3    # Retries on transient agent initialization timeout
AGENT_RETRY_BASE_DELAY = 5    # Base delay in seconds (doubles each attempt)
MAX_CONCURRENT_AGENTS  = 3    # Cap on simultaneous SDK agent sessions (≤ pipeline concurrency)

# ── Models ────────────────────────────────────────────────────────────────────

MODEL_DEFAULT  = "claude-sonnet-4-5"          # orchestrator + most stages
MODEL_FAST     = "claude-haiku-4-5-20251001"  # boundary selector (simple filtering)
MODEL_POWERFUL = "claude-opus-4-5"            # root cause annotator (deep reasoning)

# ── Output filenames ───────────────────────────────────────────────────────────

OUT_SELECTED_BOUNDARIES        = "selected_boundaries.json"
OUT_GLOBAL_SELECTED_BOUNDARIES = "global_selected_boundaries.json"
OUT_REPORT              = "unsoundness_report.json"
OUT_TEST_CASE_DIR       = "test_case"
OUT_STATICCG_DIR        = "staticcg"    # static call graph from analysis interface
OUT_DYNCG_DIR           = "dyncg"       # dynamic call graph from analysis interface
OUT_BOUNDARY_DIR        = "boundary"    # missed edges (in dyncg, not in staticcg)
OUT_ANNOTATED_TOOL_DIR  = "annotated_sa_tool"
OUT_MODIFIED_ANALYSIS   = "modified_analysis.json"  # annotated SA tool output (SCG')
OUT_EXECUTION_TRACE     = "log.txt"
OUT_PIPELINE_RESULT     = "pipeline_result.json"