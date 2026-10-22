"""
config.py
=========
All pipeline constants. This is the only file you need to edit
for tuning behaviour — no logic lives here.
"""

from pathlib import Path

# ── Loop control ───────────────────────────────────────────────────────────────

MAX_LOOPS              = 1    # Maximum outer retries (stages 3–6, only on reproduce failure)
ANNOTATION_MAX_RETRIES = 3    # Maximum inner retries for stages 7–8 (annotation + trace) only
MAX_AGENT_RETRIES      = 3    # Retries on transient agent initialization timeout
AGENT_RETRY_BASE_DELAY = 5    # Base delay in seconds (doubles each attempt)
MAX_CONCURRENT_AGENTS  = 3    # Cap on simultaneous SDK agent sessions (≤ pipeline concurrency)

# ── Oracle (spec-driven instrumentation, see oracle_agent/SPEC.md) ────────────

ORACLE_AGENT_DIR        = Path(__file__).parent / "oracle_agent"
ORACLE_AGENT_JAR        = ORACLE_AGENT_DIR / "target" / "cgrizz-oracle-agent.jar"
ORACLE_SPEC_FILE        = "oracle_spec.json"
ORACLE_RUN_TIMEOUT      = 1800  # seconds per oracle run on a real program (SIGTERM, then the oracle is still written)
ORACLE_REFINE_ATTEMPTS  = 2     # spec refinements after a failed check on the real program
ORACLE_GENERATE_ATTEMPTS = 2    # attempts to get a valid spec + non-empty oracle on the test case

# ── Models ────────────────────────────────────────────────────────────────────

MODEL_DEFAULT  = "claude-sonnet-4-5"          # orchestrator + most stages
MODEL_FAST     = "claude-haiku-4-5-20251001"  # boundary selector (simple filtering)
MODEL_POWERFUL = "claude-opus-4-8"            # root cause annotator (deep reasoning)

# ── Output filenames ───────────────────────────────────────────────────────────

OUT_SELECTED_BOUNDARIES        = "selected_boundaries.json"
OUT_GLOBAL_SELECTED_BOUNDARIES = "global_selected_boundaries.json"
OUT_REPRESENTATIVES_REGISTRY   = "representatives_registry.json"
OUT_REPORT              = "unsoundness_report.json"
OUT_ORACLE              = "oracle.json"
OUT_TEST_CASE_DIR       = "test_case"
OUT_STATICCG_DIR        = "staticcg"    # static call graph from analysis interface
OUT_DYNCG_DIR           = "dyncg"       # dynamic call graph from analysis interface
OUT_BOUNDARY_DIR        = "boundary"    # missed edges (in dyncg, not in staticcg)
OUT_ANNOTATED_TOOL_DIR  = "annotated_sa_tool"
OUT_MODIFIED_ANALYSIS   = "modified_analysis.json"  # annotated SA tool output (SCG')
OUT_EXECUTION_TRACE     = "log.txt"
OUT_PIPELINE_RESULT     = "pipeline_result.json"
OUT_TARGET_ORACLE       = "oracle_target.json"
OUT_PRUNING_REPORT      = "pruning_report.json"

# ── JVM-runtime callee exclusions (pre-sampler filter) ────────────────────────
# Tuples of (jvm_class_or_None, method_name).
# None means match any declaring class (used for <clinit>).
# Classes in JVM internal format: "Ljava/lang/Object;"
JVM_RUNTIME_EXCLUDED_CALLEES: list[tuple[str | None, str]] = [
    (None,                               "<clinit>"),
    ("Ljava/lang/Object;",               "finalize"),
    ("Ljava/lang/ref/Finalizer;",        "register"),
    ("Ljava/lang/ref/Reference;",        "enqueue"),
    ("Ljava/lang/ref/ReferenceQueue;",   "enqueue"),
    ("Lsun/misc/Cleaner;",               "clean"),            # Java 8 cleaner
    ("Ljava/lang/ref/Cleaner;",          "register"),         # Java 9+
    ("Ljava/lang/ref/Cleaner$Cleanable;","clean"),
    ("Ljava/lang/ClassLoader;",          "defineClass"),
    ("Ljava/lang/ClassLoader;",          "loadClass"),
    ("Ljava/lang/ClassLoader;",          "findClass"),
    ("Ljava/lang/ClassLoader;",      "checkPackageAccess"),
    ("Ljava/lang/SecurityManager;",      "checkPackageAccess"),
    ("Ljava/lang/SecurityManager;",      "checkMemberAccess"),
    ("Ljava/lang/invoke/MethodHandleNatives;", None) 
]