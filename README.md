# Agentic Workflow — Call-Graph Unsoundness Analysis

An agentic pipeline that explains **why static analysis (SA) call-graph tools miss
edges**. Given a *boundary* (a caller→callee edge observed dynamically but absent
from the tool's static call graph), the pipeline uses Claude agents to reason about
the root cause, build a minimal test case that reproduces it, confirm the cause with
runtime instrumentation and by annotating the SA tool itself, and finally group all
other boundaries that share the same root cause.

Tools covered: **WALA** (`0-CFA`), **Soot** (`SPARK`), **OPAL** (`0-CFA`).
Benchmarks: **cats**, **njr**, **xcorpus**.

---

## How it works

Each boundary is processed as one pipeline run driven by `PipelineState` (the single
source of truth — see `pipeline/state.py`). The orchestrator wires the stages together
with retry loops in `pipeline/runner.py` (stage functions live in `pipeline/stages/`):

| Stage | What it does | Agent |
|-------|--------------|-------|
| 3. Generate report | Reason about why the edge was missed → `unsoundness_report.json` | `fn-edge-analyzer` |
| 4. Generate test case | Two minimal Maven projects: one reproducing the unsoundness, one without the root cause | `test-case-generator` |
| 5. Generate oracle | Write an ASM Java-agent that observes the root-cause pattern at runtime → `oracle.json` | `instrumentation` |
| 5b. Check oracle on real program | Run the instrumentation JAR against the actual program; confirm the boundary edge shows up as a symptom | — |
| 6. Reproduction check | Run the SA tool on the test case (via `jcg/analysis_interface.py`) and confirm the edge is genuinely missed | — |
| 7. Root-cause annotation | Inject one targeted `[UNSOUNDNESS]` log into the SA tool's source at the failure point, then re-run it | `root-cause-annotator` |
| 8. Trace check | Verify the annotated tool's execution trace fired the `[UNSOUNDNESS]` log for the expected method | — |
| 9. Refine report | Update the report with the confirmed code location | `fn-edge-analyzer` |

If a check fails, feedback is accumulated into `PipelineState` and the relevant stages
are retried (loop counts in `config.py`: `MAX_LOOPS`, `ANNOTATION_MAX_RETRIES`).

**Oracle deduplication.** After a boundary succeeds, its instrumentation JAR is run
against every remaining boundary; any boundary whose missed edge also appears as a
symptom is *pruned* (it shares the same root cause). Each successful boundary therefore
defines an **equivalence group**, recorded in `pruning_report.json`. This is what makes
the analysis scale: one reasoned boundary can explain many.

### Code layout

```
pipeline_orchestrator.py   Entry point; CLI and the three modes
config.py                  All constants (models, retries, output filenames)
pipeline/
  state.py                 PipelineState dataclass (all in-flight data)
  runner.py                run_pipeline(): the retry loops that wire the stages together
  stages/                  One module per stage family
    report.py              3. generate report, 9. refine report (+ retry feedback)
    test_case.py           4. test case generation
    oracle.py              5. oracle generation, 5b. oracle check on the real program
    reproduction.py        6. reproduction check
    annotation.py          7. root-cause annotation, 8. trace check
  agents.py                Builds ClaudeAgentOptions (auto-loads .claude/agents/*)
  agent_runner.py          run_agent() and JSON parsing of agent responses
  edges.py                 Edge / method-name matching across boundary, CG and trace formats
  oracle.py                Locating and running the oracle agent; reading symptoms
  dedup.py                 Oracle deduplication (post-success and pre-run) and pruning reports
  boundaries.py            PipelineState construction, boundaries.json loading, resume filter
  outcome.py               pipeline_result.json and run summaries
  analysis.py              Invokes the SA tool / annotated tool via jcg/*
  sampler.py               Pre-filters JVM-runtime and JDK-caller boundaries
  logs.py                  Logging configuration
.claude/agents/            The four subagent definitions (Markdown + frontmatter)
jcg/                       analysis_interface.py / annotation_interface.py — run WALA/Soot/OPAL
scripts/                   helpers/ and paper/ — results aggregation (RQ1, RQ2, runtime)
input_data/<bench>/        boundaries.json per program/tool/algorithm
programs/<bench>/          target programs and JARs
tools/{wala,soot,opal}/    SA tool sources (copied + annotated per boundary)
```

---

## Results

Output is written under `--output-dir`, one directory per boundary:

```
results/<program>/<tool>/<algorithm>/b<i>_<Caller.method>__<Callee.method>/
├── unsoundness_report.json   reasoning: edgeId, caller, target, pattern_class,
│                             evidence, toolLimitation, potentialTestCase
├── test_case/
│   ├── reproducing/          minimal Maven project (+ oracle.json, instrumentation/)
│   └── non_reproducing/      same minus the root cause
├── annotated_sa_tool/        SA tool source with the injected [UNSOUNDNESS] log
├── oracle_target.json        oracle run against the real program
├── pruning_report.json       boundaries grouped under this one (equivalence group)
├── pipeline_result.json      final summary: success, reason, identity, artifacts, feedback
└── pipeline.log              per-boundary log
```

`pipeline_result.json` is the authoritative status (`"success": true/false`). The
`scripts/paper/` programs aggregate these across all runs to produce the paper's tables
and charts:

- **`RQ1_table.py` / `RQ1_chart.py` / `RQ1_feedback.py`** — per (benchmark, tool)
  counts of *Reasoned* (succeeded), *Grouped* (pruned by a successful boundary), and
  *Failed*.
- **`RQ2.py`** — analysis of the equivalence groups (root-cause families).
- **`runtime_analysis.py`** — wall-clock time per boundary from `pipeline.log` timestamps.

---

## How to run

### Prerequisites

```bash
pip install -r requirements.txt   # claude-agent-sdk
```

You also need a working Java/Maven toolchain (agents build and run test cases), and the
SA tools under `tools/` buildable by `jcg/analysis_interface.py`. Agents are
auto-discovered from `.claude/agents/`; `.claude/settings.json` grants the tool
permissions they need.

### Configuration

A `pipeline_config.json` declares the `(program, tool, algorithm)` triples to analyze.
Ready-made configs live in `ignored_stuff/configs/` (e.g.
`pipeline_config_xcorpus_Soot_SPARK.json`). Each entry:

```json
{
  "analyses": [
    {
      "program_name":   "checkstyle-5.1",
      "target_program": "../programs/xcorpus/checkstyle-5.1",
      "sa_tool_name":   "Soot",
      "sa_algorithm":   "SPARK",
      "sa_tool":        "../tools/soot",
      "boundaries_file":"../input_data/xcorpus/checkstyle-5.1/Soot/SPARK/boundaries.json",
      "main_class":     "EntryPoint_builtinTest",
      "program_jar_path":"../programs/xcorpus/checkstyle-5.1/temp"
    }
  ]
}
```

### Modes

```bash
# 1. single — one boundary by index (debugging)
python pipeline_orchestrator.py single \
    --program-name     checkstyle-5.1 \
    --target-program   ../programs/xcorpus/checkstyle-5.1 \
    --main-class       EntryPoint_builtinTest \
    --sa-tool-name     Soot --sa-algorithm SPARK \
    --sa-tool          ../tools/soot \
    --boundaries       ../input_data/xcorpus/checkstyle-5.1/Soot/SPARK/boundaries.json \
    --boundary-index   0 \
    --program_jar_path ../programs/xcorpus/checkstyle-5.1/temp \
    --output-dir       ../results

# 2. parallel-edges — all boundaries of one analysis entry (sequential + dedup)
python pipeline_orchestrator.py parallel-edges \
    --config ./pipeline_config.json \
    --program-name checkstyle-5.1 --tool-name Soot --algorithm SPARK \
    --output-dir ../results

# 3. parallel-all — every analysis entry in the config (sequential + dedup)
python pipeline_orchestrator.py parallel-all \
    --config ./pipeline_config.json \
    --output-dir ../results
```

**Resuming & cross-run dedup** (modes 2/3): if `--output-dir` already has results, the
run skips already-analyzed and already-pruned boundaries automatically. Pass
`--prev-results <dir>...` to pre-prune the new queue using a prior run's instrumentation
(or add `--report-prededup` to prune from existing `pruning_report.json` files without
re-running anything).

> Note: "parallel" modes currently process boundaries **sequentially** so that oracle
> deduplication can prune the remaining queue after each success. Concurrency lives at
> the agent level (`MAX_CONCURRENT_AGENTS` in `config.py`).

### Tuning (`config.py`)

`MAX_LOOPS`, `ANNOTATION_MAX_RETRIES`, agent-retry backoff, and the model assignment:
`MODEL_DEFAULT` (Sonnet), `MODEL_FAST` (Haiku), `MODEL_POWERFUL` (Opus). Output
filenames are all defined here too.
</content>
</invoke>
