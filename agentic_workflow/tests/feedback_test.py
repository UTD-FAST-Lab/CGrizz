"""
Tests for the feedback the agents get when a check fails: the reproduction
check's diagnosis, the oracle verdict as seen by the report agent, and the
feedback block of the report prompt.

    cd agentic_workflow && python tests/feedback_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.edges import edge_in_cg
from pipeline.oracle import Verdict, PATTERN
from pipeline.stages.oracle import report_feedback_from_verdict
from pipeline.stages.report import _build_feedback_section
from pipeline.stages.reproduction import _not_reproduced
from pipeline.state import PipelineState

CALLER, CALLEE = "com.example.App.main([Ljava/lang/String;)V", "com.example.App$Impl.run()V"


def cg(path: Path, with_edge: bool) -> str:
    targets = [{"declaringClass": "Lcom/example/App$Impl;", "name": "run"}] if with_edge else []
    path.write_text(json.dumps({"reachableMethods": [
        {"method": {"declaringClass": "Lcom/example/App;", "name": "main"},
         "callSites": [{"declaredTarget": {"declaringClass": "Lcom/example/App$Svc;", "name": "run"},
                        "line": 7, "pc": 3, "targets": targets}]}]}))
    return str(path)


def state() -> PipelineState:
    d = Path(tempfile.mkdtemp())
    s = PipelineState(program_name="prog", tool_name="WALA", algorithm="0-CFA", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=".", output_dir=d,
                      boundary={"caller": {"declaringClass": "Lorg/app/Main;", "name": "main"},
                                "missedCallee": {"declaringClass": "Lorg/app/Plugin;", "name": "start"}})
    (d / "boundaries.json").write_text(json.dumps({"boundaries": [
        {"caller": {"declaringClass": "Lcom/example/App;", "name": "main"},
         "missedCallee": {"declaringClass": "Lcom/example/App$Other;", "name": "go"}}]}))
    s.boundary_path = str(d / "boundaries.json")
    return s


def test_edge_in_cg():
    d = Path(tempfile.mkdtemp())
    assert edge_in_cg(cg(d / "a.json", True), CALLER, CALLEE) is True
    assert edge_in_cg(cg(d / "b.json", False), CALLER, CALLEE) is False
    assert edge_in_cg(str(d / "missing.json"), CALLER, CALLEE) is None


def test_not_reproduced():
    s = state()
    d = Path(tempfile.mkdtemp())
    s.dyncg_path, s.staticcg_path = cg(d / "dyn.json", True), cg(d / "static.json", True)
    fb = _not_reproduced(s, CALLER, CALLEE)
    assert "executed at run time (dynamic call graph): yes" in fb
    assert "resolved by the tool (static call graph): yes" in fb
    assert "WALA handles the test case's version of the mechanism" in fb
    assert "com.example.App.main → com.example.App$Other.go" in fb       # what the tool does miss

    s.dyncg_path = cg(d / "dyn2.json", False)
    fb = _not_reproduced(s, CALLER, CALLEE)
    assert "never executes this call" in fb and "executed at run time (dynamic call graph): no" in fb


def test_report_feedback_from_verdict():
    s = state()
    s.oracle_spec_path = "/x/oracle_spec.json"
    fb = report_feedback_from_verdict(s, Verdict("edge_missing", PATTERN, "Symptoms at the same caller: [a → b]"))
    assert fb.startswith("[oracle on the real program]")
    assert "org.app.Main.main → org.app.Plugin.start" in fb
    assert "affects other calls, but not the missed call" in fb and "Symptoms at the same caller" in fb
    assert "The root cause hypothesis is not correct" not in fb


def test_feedback_section():
    s = state()
    assert _build_feedback_section(s) == ""
    s.reproduce_feedback = ["[oracle on the real program] A", "[reproduction check] B"]
    s.trace_feedback = ["[annotation / trace check] C"]
    text = _build_feedback_section(s)
    assert "### Attempt 1\n[oracle on the real program] A" in text and "### Attempt 2\n[reproduction check] B" in text
    assert "- [annotation / trace check] C" in text


if __name__ == "__main__":
    test_edge_in_cg()
    test_not_reproduced()
    test_report_feedback_from_verdict()
    test_feedback_section()
    print("feedback: all tests passed")
