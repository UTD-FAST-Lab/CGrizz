# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Agentic Workflow** is an unsoundness analysis pipeline that uses Claude agents to reason about why static analysis tools miss certain function calls. The pipeline:

1. Takes boundaries (places where SA tools missed dynamically-observed callees)
2. Generates test cases to reproduce the unsoundness
3. Produces detailed reasoning reports explaining the root causes
4. Annotates the SA tool to trace execution and compare call graphs

The system supports three execution modes:
- **single** — analyze one boundary entry
- **parallel-edges** — analyze all boundaries for one (program, tool, algorithm) concurrently
- **parallel-all** — analyze all combinations concurrently

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

# All boundaries for one analysis, concurrent
python pipeline_orchestrator.py parallel-edges \
    --config      ./pipeline_config.json \
    --program-name program_A \
    --tool-name   WALA \
    --algorithm   0-CFA \
    --concurrency 5 \
    --output-dir  ./results

# All programs × all edges, concurrent
python pipeline_orchestrator.py parallel-all \
    --config      ./pipeline_config.json \
    --concurrency 5 \
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
results/
└── program_A/
    └── WALA/
        └── 0-CFA/
            └── b0_Handler.dispatch__ConcreteHandler.handlePost/
                ├── unsoundness_report.md      # reasoning about root cause
                ├── test_case/                  # generated reproducible test
                ├── test_analysis.json          # SA tool output on test case
                ├── annotated_sa_tool/          # instrumented SA tool directory
                ├── modified_analysis.json      # SA tool output (with annotations)
                ├── execution_trace.log         # trace from annotated tool
                └── pipeline_result.json        # final result summary
```

**Constants & Tuning (`config.py`):**
- `MAX_LOOPS` — maximum retry attempts if stages fail
- `MODEL_DEFAULT` — model for orchestrator and most stages
- `MODEL_FAST` — model for simple filtering (e.g., boundary selector)
- `MODEL_POWERFUL` — model for deep reasoning (e.g., root cause annotator)
- Output filename constants

## Stage-Level Details

Each stage is an async function that takes `PipelineState`, calls `run_agent()`, and mutates state:

1. **stage_generate_report** — main agent analyzes the boundary and produces a reasoning report
2. **stage_generate_test_case** — generates a minimal Java program that reproduces the unsoundness
3. **stage_validate_test_case** — checks the test case compiles and reproduces the boundary
4. **stage_trace_execution** — annotates SA tool to produce execution trace
5. **stage_root_cause_analysis** — deep reasoning on trace to explain why unsoundness occurs

Stages accumulate feedback across retry loops (stored in `state.reproduce_feedback` and `state.trace_feedback`) and inject it into the prompt on retry.

## Implementation Notes

- **SA Tool Integration (`pipeline/analysis.py`):** The stubs `run_sa_on_test_case()` and `run_annotated_sa()` must be implemented to invoke your actual SA tool (e.g., via `subprocess.run()` for Java tools). These are the only places where tool-specific logic lives.
- **Agent Discovery:** Agents are auto-discovered from `.claude/agents/` (project-level) and `~/.claude/agents/` (user-level). Define custom agents as Markdown files with frontmatter in those directories.
- **Async Loop:** The orchestrator is async and uses `asyncio` for concurrent task execution.
- **Logging:** All modules use Python's standard `logging` with loggers named `pipeline.*`. Use `log.info()` and `log.error()` with `state.label()` for consistency.

## Testing

Currently, there are no automated tests. Validation happens through:
1. Stage success/fail returns in `run_pipeline()`
2. Retry logic with accumulated feedback
3. Final result written to `pipeline_result.json`

To debug a single boundary, run the `single` mode with verbose logging and inspect intermediate output files.
