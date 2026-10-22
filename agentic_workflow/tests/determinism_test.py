"""
Tests for the report format check, the schema repair round in stage 3, and
scripts/helpers/reproducibility.py (#5).

    cd agentic_workflow && python tests/determinism_test.py
"""
import asyncio
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "scripts/helpers"))

import pipeline.stages.report as RS
from pipeline.report_schema import validate, tool_location
from pipeline.state import PipelineState
import reproducibility as REPRO

VALID = {
    "edgeId": "E1", "caller": "a.B.c()", "callerLine": 3, "target": "d.E.f()",
    "evidence": ["src/a/B.java:3 — loads the class by a non-constant name"],
    "toolLimitation": {"reason": "r", "rootCause": "missing_model",
                       "toolCodeEvidence": {"relevantFiles": ["core/src/Resolver.java:98"], "codeSnippet": "s"}},
    "potentialTestCase": {"description": "d", "TestCaseMissingEdge": {"caller": "a", "callee": "b"}},
}


def tool_tree() -> Path:
    tool = Path(tempfile.mkdtemp()) / "wala"
    (tool / "core/src").mkdir(parents=True)
    (tool / "core/src/Resolver.java").write_text("class Resolver {}")
    return tool


def test_schema():
    assert validate(VALID) == []
    assert tool_location("core/src/Resolver.java:98 — else branch") == ("core/src/Resolver.java", 98)
    assert tool_location("Resolver.java (line 98)") is None

    bad = copy.deepcopy(VALID)
    bad["pattern_class"] = "reflection"                 # no longer part of the report
    bad["toolLimitation"]["rootCause"] = "bug"
    bad["evidence"] = []
    del bad["potentialTestCase"]["TestCaseMissingEdge"]
    problems = validate(bad)
    assert any("unexpected field `pattern_class`" in p for p in problems)
    assert any("rootCause" in p for p in problems)
    assert any("`evidence` must list" in p for p in problems)
    assert any("potentialTestCase.TestCaseMissingEdge" in p for p in problems)
    assert validate([]) == ["the report must be a JSON object"]


def test_tool_locations_exist():
    tool = tool_tree()
    assert validate(VALID, tool) == []
    prefixed = copy.deepcopy(VALID)
    prefixed["toolLimitation"]["toolCodeEvidence"]["relevantFiles"] = ["tools/wala/core/src/Resolver.java:98"]
    assert validate(prefixed, tool) == [], "a leading tool-dir prefix is tolerated"
    missing = copy.deepcopy(VALID)
    missing["toolLimitation"]["toolCodeEvidence"]["relevantFiles"] = ["core/src/Nope.java:1", "Resolver line 98"]
    problems = validate(missing, tool)
    assert any("Nope.java does not exist" in p for p in problems)
    assert any("must start with" in p for p in problems)


def test_schema_repair_round():
    out = Path(tempfile.mkdtemp())
    report = out / "unsoundness_report.json"
    bad = copy.deepcopy(VALID)
    bad["pattern_class"] = "reflection"
    report.write_text(json.dumps(bad))
    prompts = []

    async def fixing_agent(prompt, options, stage_name):
        prompts.append(prompt)
        report.write_text(json.dumps(VALID))
        return "{}"
    RS.run_agent, RS.build_agent_options = fixing_agent, (lambda s: None)
    s = PipelineState(program_name="p", tool_name="WALA", algorithm="0-CFA", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=str(tool_tree()), boundary={},
                      output_dir=out)
    s.unsoundness_report = str(report)
    asyncio.run(RS._enforce_schema(s))
    assert len(prompts) == 1 and "unexpected field `pattern_class`" in prompts[0] and s.report_schema_problems == []
    assert "pattern_class" not in prompts[0].split("Keep the analysis; only fix:")[0]

    async def stubborn_agent(prompt, options, stage_name):
        prompts.append(prompt)
        return "{}"
    report.write_text(json.dumps(bad))
    RS.run_agent = stubborn_agent
    asyncio.run(RS._enforce_schema(s))
    assert len(prompts) == 2 and s.report_schema_problems, "problems are recorded, not fatal"


def make_results(root: Path, outcomes: dict, groups: dict):
    """outcomes: boundary → (success, tool location, annotated file); groups: representative → [pruned]"""
    for b, (success, tool_loc, ann) in outcomes.items():
        d = root / "prog/Soot/SPARK" / b
        d.mkdir(parents=True)
        (d / "pipeline_result.json").write_text(json.dumps({
            "success": success,
            "report": {"tool_locations": [tool_loc] if tool_loc else [], "evidence_locations": ["src/App.java:9"],
                       "rootCause": "missing_model"},
            "annotation": {"file": ann, "line": 10}}))
        if b in groups:
            (d / "pruning_report.json").write_text(json.dumps({"pruned_boundaries": [
                {"program_name": "prog", "tool_name": "Soot", "algorithm": "SPARK", "boundary_id": p}
                for p in groups[b]]}))


def test_reproducibility():
    r1, r2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    make_results(r1, {"b0": (True, "soot/Proxy.java:10", "A.java"), "b3": (True, "soot/Unsafe.java:5", "U.java"),
                      "b5": (False, None, None)}, {"b0": ["b1", "b2"]})
    make_results(r2, {"b0": (True, "soot/Proxy.java:12", "A.java"), "b3": (True, "soot/Native.java:5", "U.java"),
                      "b1": (True, "soot/Proxy.java:10", "B.java"), "b5": (False, None, None)}, {"b0": ["b2"]})
    a, b = REPRO.load_run(r1), REPRO.load_run(r2)
    assert a[("prog", "Soot", "SPARK", "b1")]["status"] == "grouped"
    c = REPRO.compare(a, b)
    assert c["boundaries"] == 5 and c["status"] == (4, 5)       # b1: grouped vs reasoned
    assert c["tool_files"] == (1, 2)                            # b0 same file (other line), b3 different file
    assert c["evidence_files"] == (2, 2) and c["annotation_file"] == (2, 2)
    # run 1 groups {b0,b1,b2}; run 2 groups {b0,b2}, b1 alone → co-grouped pairs: 3 vs 1, 1 shared
    assert c["co_grouped_pairs"] == 3 and abs(c["grouping_jaccard"] - 1 / 3) < 1e-9
    out = subprocess.run([sys.executable, str(REPO / "scripts/helpers/reproducibility.py"), str(r1), str(r2)],
                         capture_output=True, text=True, check=True).stdout
    assert "4/5 (80%)" in out and "1/2 (50%)" in out and "tool files" in out, out


def test_reproducibility_reads_old_results():
    r = Path(tempfile.mkdtemp())
    d = r / "prog/WALA/0-CFA/b0"
    d.mkdir(parents=True)
    (d / "pipeline_result.json").write_text(json.dumps({"success": True}))
    (d / "unsoundness_report.json").write_text(json.dumps(VALID))
    run = REPRO.load_run(r)
    assert run[("prog", "WALA", "0-CFA", "b0")]["tool_files"] == frozenset({"core/src/Resolver.java"})


if __name__ == "__main__":
    test_schema()
    test_tool_locations_exist()
    test_schema_repair_round()
    test_reproducibility()
    test_reproducibility_reads_old_results()
    print("determinism: all tests passed")
