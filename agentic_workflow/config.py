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

# ── Tool warning checks (pipeline/warning_checks.py) ───────────────────────────

ANNOTATION_MAX_CALLER_FRACTION = 0.05  # [UNSOUNDNESS] may fire for at most this share of reachable methods
ANNOTATION_NEGATIVE_CHECK      = True  # also run the annotated tool on the non-reproducing test case
ANNOTATION_MAX_DESCRIPTION_CHARS = 60  # the problem description printed by [UNSOUNDNESS]

# ── Oracle (spec-driven instrumentation, see oracle_agent/SPEC.md) ────────────

ORACLE_AGENT_DIR        = Path(__file__).parent / "oracle_agent"
ORACLE_AGENT_JAR        = ORACLE_AGENT_DIR / "target" / "cgrizz-oracle-agent.jar"
ORACLE_SPEC_FILE        = "oracle_spec.json"
ORACLE_RUN_TIMEOUT      = 1800  # seconds per oracle run on a real program (SIGTERM, then the oracle is still written)
ORACLE_REFINE_ATTEMPTS  = 2     # spec refinements after a failed check on the real program
ORACLE_GENERATE_ATTEMPTS = 2    # attempts to get a valid spec + non-empty oracle on the test case
ORACLE_NEGATIVE_CHECK   = True  # the spec must produce no symptoms on the non-reproducing (near-miss) test case

# ── Test case size (pipeline/test_case_size.py, issue #3) ──────────────────────

TEST_CASE_MAX_FILES          = 6    # budget for the reproducing test case (src/main/java)
TEST_CASE_MAX_LINES          = 150  # lines of code, without blanks and comments
TEST_CASE_SIMPLIFY_ATTEMPTS  = 1    # "simplify" rounds when a new test case is over budget
TEST_CASE_SHRINK_ABOVE_LINES = 60   # after reproduction passes, try to shrink test cases larger than this
TEST_CASE_SHRINK_ATTEMPTS    = 1    # each attempt re-runs the oracle checks and the reproduction check

# ── Reports (pipeline/report_schema.py) ───────────────────────────────────────

REPORT_SCHEMA_RETRIES = 1    # times the analyzer may fix a report that does not match the schema

# ── JCG containers (jcg/) ──────────────────────────────────────────────────────

# Opt-in until validated on your setup (jcg/validate_fast_path.py): run JCG mains
# with java on the classpath exported at image build time instead of sbt, and for
# WALA/Soot compile only the annotated files on top of the prebuilt tool instead
# of rebuilding the tool and JCG for every trace check. OPAL always rebuilds.
JCG_FAST_PATH = False

# ── Models ────────────────────────────────────────────────────────────────────

MODEL_DEFAULT  = "claude-sonnet-4-5"          # not used by the pipeline sessions (see MODEL_PIPELINE)
MODEL_FAST     = "claude-haiku-4-5-20251001"  # boundary selector (simple filtering)
MODEL_POWERFUL = "claude-opus-4-8"            # deep reasoning

# The model every pipeline session (and its subagents, which set no model of
# their own) runs on — pipeline/agents.py. One pinned model keeps runs comparable.
MODEL_PIPELINE = MODEL_POWERFUL

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