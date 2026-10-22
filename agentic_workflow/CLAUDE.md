# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Agentic Workflow** explains why static analysis (SA) call-graph tools (WALA, Soot, OPAL) miss
call edges. For each *boundary* — an edge seen at run time but missing from the tool's call
graph — Claude agents and deterministic checks:

1. write an unsoundness report: the evidence in the program, the tool limitation (tool code that
   drops the edge) and a minimal test case description;
2. generate a reproducing test case and a non-reproducing *near miss*;
3. write an oracle spec that the prebuilt oracle agent (`oracle_agent/`) uses to observe the root
   cause and its symptoms, on the test cases and on the real program;
4. check the edge is missed on the test case, annotate the SA tool with one `[UNSOUNDNESS]`
   warning and check it fires only for this root cause;
5. group every other boundary whose edge the oracle reports as a symptom.

Modes (`pipeline_orchestrator.py`): **single** (one boundary), **parallel-edges** (all boundaries of
one program/tool/algorithm) and **parallel-all** (everything in the config). Despite the names,
boundaries run sequentially so that deduplication can prune the queue after each success; the
order is deterministic (config order, then `boundaries.json` order).

## Architecture

The pipeline is organized into stages (`pipeline/stages/`) wired together by `pipeline/runner.py`; `pipeline_orchestrator.py` builds the queues and runs them. All data flows through a single `PipelineState` object (not passed as loose arguments):

- **`pipeline_orchestrator.py`** — entry point; CLI and the three modes
- **`pipeline/state.py`** — `PipelineState` dataclass; single source of truth for all in-flight data
- **`pipeline/runner.py`** — `run_pipeline()`: the retry loops that wire the stages together
- **`pipeline/stages/`** — stage functions, one module per stage family (`report`, `test_case`, `oracle`, `reproduction`, `annotation`); each calls `run_agent()` and mutates state
- **`pipeline/agents.py`** — factory for `ClaudeAgentOptions`; loads agents from `.claude/agents/`
- **`pipeline/agent_runner.py`** — `run_agent()` executor; handles agent invocation and JSON parsing
- **`pipeline/edges.py`** — matching of edges / method names across boundary, CG and trace formats
- **`pipeline/oracle.py`** — building/running the oracle agent, spec lint, verdict classification, symptoms
- **`tool_markers/<tool>/`** — unsoundness-marker modules for Soot, WALA and OPAL: installed into the annotated tool, they turn one `Unsound.mark(...)` at the root cause into `[UNSOUNDNESS-EDGE]` warnings at every affected call (`pipeline/tool_markers.py`)
- **`oracle_agent/`** — the prebuilt Java oracle agent; the instrumentation agent only writes `oracle_spec.json` (see `oracle_agent/SPEC.md`)
- **`pipeline/dedup.py`** — oracle deduplication and pruning reports
- **`pipeline/boundaries.py`** — building states from boundaries.json, resume filter
- **`pipeline/outcome.py`** — `pipeline_result.json` and run summaries
- **`pipeline/analysis.py`** — wrappers for running the SA tool on test cases (via `jcg/`)
- **`config.py`** — all pipeline constants (models, output filenames, retry limits)
- **`pipeline_config.json`** — declares which (program, tool, algorithm) triples to analyze

## Common Commands

### Running the Pipeline

```bash
# Single boundary analysis
python pipeline_orchestrator.py single \
    --program-name   program_A \
    --target-program ./programs/A \
    --sa-tool-name   WALA \
    --sa-algorithm   0-CFA \
    --sa-tool        ./tools/wala \
    --boundaries     ./input_data/program_A/WALA/0-CFA/boundaries.json \
    --boundary-index 0 \
    --output-dir     ./results

# All boundaries for one analysis (sequential, with dedup)
python pipeline_orchestrator.py parallel-edges \
    --config      ./pipeline_config.json \
    --program-name program_A \
    --tool-name   WALA \
    --algorithm   0-CFA \
    --output-dir  ./results

# All programs × all edges (sequential, with dedup)
python pipeline_orchestrator.py parallel-all \
    --config      ./pipeline_config.json \
    --output-dir  ./results
```

### Dependencies

The project requires `claude-agent-sdk` (see `requirements.txt`). Install with:
```bash
pip install -r requirements.txt
```

## Data Flow & Configuration

**Input (pipeline_config.json):**
Each analysis entry declares a (program, tool, algorithm) triple:
```json
{
  "analyses": [
    {
      "program_name":    "program_A",
      "target_program":  "./programs/A",
      "sa_tool_name":    "WALA",
      "sa_algorithm":    "0-CFA",
      "sa_tool":         "./tools/wala",
      "boundaries_file": "./input_data/program_A/WALA/0-CFA/boundaries.json"
    }
  ]
}
```

Each `boundaries_file` is a JSON array of boundary entries. A boundary entry describes:
- **caller** — method in the target program that made a call
- **missedCallee** — method that was invoked dynamically but missed by the SA tool
- **staticCalleesAtSite** — what the SA tool resolved statically at that call site
- **tracesLeadingToBoundary** — execution traces from entry points to the boundary
- **impact** — severity information

**Output (per boundary):**
```
results/<program>/<tool>/<algorithm>/b<i>_<Caller.method>__<Callee.method>/
├── unsoundness_report.json      edgeId, caller, target, evidence, toolLimitation, potentialTestCase
├── test_case/
│   ├── reproducing/             Maven project, jcg.conf, oracle.json, instrumentation/oracle_spec.json,
│   │                            staticcg/ dyncg/ boundary/ (JCG outputs; staticcg/log.txt = tool trace)
│   └── non_reproducing/         the near miss (optional)
├── annotated_sa_tool/           copy of the SA tool with the [UNSOUNDNESS] log
├── oracle_target.json           the spec's oracle on the real program (+ .stats.json, .log)
├── pruning_report.json          boundaries grouped under this one
├── pipeline_result.json         success, reason, failure_kind, report summary, annotation,
│                                oracle_verdicts, stage_seconds, feedback_history
└── pipeline.log
```

**Constants (`config.py`)** — loop limits (`MAX_LOOPS`, `ANNOTATION_MAX_RETRIES`,
`ORACLE_REFINE_ATTEMPTS`, `ORACLE_GENERATE_ATTEMPTS`, `REPORT_SCHEMA_RETRIES`), check settings
(`ORACLE_NEGATIVE_CHECK`, `ANNOTATION_NEGATIVE_CHECK`, `ANNOTATION_MAX_CALLER_FRACTION`,
`ANNOTATION_MAX_DESCRIPTION_CHARS`, `TEST_CASE_*`), `ORACLE_RUN_TIMEOUT`, `JCG_FAST_PATH`, the pinned
`MODEL_PIPELINE`, and output filenames.

## Stage-Level Details

`pipeline/runner.py` runs, per boundary (stage functions in `pipeline/stages/`, each `@timed`):

| Stage | Function | Checked by the pipeline |
|---|---|---|
| 3 report | `stage_generate_report` (fn-edge-analyzer) | format (`report_schema.py`), tool locations exist |
| 4 test cases | `stage_generate_test_case` (test-case-generator) | size budget (`TEST_CASE_MAX_FILES/LINES`) → one simplify round |
| 5 oracle | `stage_generate_oracle` (instrumentation) | spec lint; symptoms on the reproducing test case; none on the near miss |
| 5b oracle on program | `stage_check_oracle_on_program` | verdict: found / pattern / spec / infrastructure / unsupported |
| 6 reproduction | `stage_check_reproduction` | each test-case symptom is a missed edge (JCG) |
| 6b shrink | `stage_shrink_test_case` (test-case-generator) | smaller version kept only if the oracle checks and reproduction still pass |
| 7 annotation | `stage_root_cause_annotation` (root-cause-annotator) | — |
| 8 trace | `stage_check_trace` | fires for the root-cause methods; short description; line/pc of the call; call graph unchanged; with a marker module every missed edge warned; ≤ 5% of methods; not on the near miss; records `warning_coverage` |
| 9 refine | `stage_refine_report` | — |

Failures become feedback for the next attempt: `oracle_feedback` (instrumentation agent),
`reproduce_feedback` (report agent; oracle and reproduction failures), `trace_feedback`
(annotator, and the report agent on outer retries). Every message names the check that failed,
what it observed and what that means. Oracle failures that say nothing about the hypothesis
(timeout, run failure, unsupported pattern) end the boundary with `failure_kind` instead of
sending the report back.

## Implementation Notes

- **SA tool integration:** `pipeline/analysis.py` calls `jcg/analysis_interface.py` (static/dynamic CG and boundaries in Docker) and `jcg/annotation_interface.py` (the annotated tool). The images are built once per run.
- **Agent Discovery:** Agents are auto-discovered from `.claude/agents/` (project-level) and `~/.claude/agents/` (user-level). Define custom agents as Markdown files with frontmatter in those directories.
- **Async:** stages are `async`; agent sessions are capped by `MAX_CONCURRENT_AGENTS`, boundaries run one at a time.
- **Logging:** All modules use Python's standard `logging` with loggers named `pipeline.*`. Use `log.info()` and `log.error()` with `state.label()` for consistency.

## Testing

No SA tools, Docker or LLM needed (Java 8 + Maven for the oracle agent):

```bash
oracle_agent/test.sh                 # oracle agent: example programs under -Xverify:all
python tests/oracle_e2e.py           # oracle stages, dedup, runner routing (stubbed LLM)
python tests/warning_checks_test.py  # [UNSOUNDNESS] checks and stage 8
python tests/feedback_test.py        # feedback messages
python tests/test_case_size_test.py  # test-case budget and shrinking
python tests/edge_mapping_test.py    # warnings ↔ boundaries (scripts/helpers/warning_coverage.py)
python tests/tool_markers_test.py    # marker installer, non-interference / coverage checks
tool_markers/run_test.sh <tool>      # a marker module on the real, pinned tool (needs tools/<tool>)
python tests/determinism_test.py     # report format, reproducibility script
python tests/jcg_interfaces_test.py  # JCG interfaces with fake docker/mvn
```

To debug one boundary, run the `single` mode and read its `pipeline.log` and `pipeline_result.json`.
`jcg/validate_fast_path.py` checks `JCG_FAST_PATH` against the default path on a finished boundary.
