"""
The reproduction check's symptom comparison (check_symptoms): every symptom a missed edge;
some missed and the rest resolved by the tool → the spec is too broad (feedback for the
instrumentation agent); nothing missed → the report goes back.

    cd agentic_workflow && python tests/reproduction_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.stages.reproduction import check_symptoms
from pipeline.state import PipelineState

MAKE = "com.example.App.make(Ljava/lang/String;)Ljava/lang/Object;"
MAIN = "com.example.App.main([Ljava/lang/String;)V"
NEW_INSTANCE = "java.lang.Class.newInstance()Ljava/lang/Object;"
TO_STRING = "com.example.Greeter.toString()Ljava/lang/String;"


def method(cls: str, name: str) -> dict:
    return {"declaringClass": f"L{cls.replace('.', '/')};", "name": name}


def make_state(symptoms: list[tuple[str, str]], static_edges: list[tuple[str, str, str, str]]) -> PipelineState:
    d = Path(tempfile.mkdtemp())
    oracle = d / "oracle.json"
    oracle.write_text(json.dumps({"RC:x": {"symptoms": [{"caller": c, "callee": e} for c, e in symptoms]}}))
    methods = {}
    for cc, cm, ec, em in static_edges + [("com.example.App", "main", "com.example.App", "make")]:
        m = methods.setdefault((cc, cm), {"method": method(cc, cm), "callSites": []})
        m["callSites"].append({"line": 1, "pc": -1, "targets": [method(ec, em)]})
    (d / "cg.json").write_text(json.dumps({"reachableMethods": list(methods.values())}))
    (d / "boundaries.json").write_text(json.dumps({"boundaries": [
        {"caller": method("com.example.App", "main"), "missedCallee": method("com.example.Greeter", "toString")}]}))
    s = PipelineState(program_name="p", tool_name="WALA", algorithm="0-CFA", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=".", boundary={}, output_dir=d)
    s.oracle_path, s.staticcg_path, s.dyncg_path = str(oracle), str(d / "cg.json"), str(d / "cg.json")
    s.boundary_path = str(d / "boundaries.json")
    return s


def test_all_missed():
    s = make_state([(MAIN, TO_STRING)], [])
    assert check_symptoms(s) == (True, "Reproduction check passed — edge found in boundary.")
    assert s.spec_too_broad_feedback is None


def test_spec_too_broad():
    s = make_state([(MAKE, NEW_INSTANCE), (MAIN, TO_STRING)], [("com.example.App", "make", "java.lang.Class", "newInstance")])
    ok, fb = check_symptoms(s)
    assert not ok and fb == s.spec_too_broad_feedback
    assert f"resolved: {MAKE} → {NEW_INSTANCE}" in fb and f"missed:   {MAIN} → {TO_STRING}" in fb
    assert "receiverClass" in fb


def test_nothing_missed():
    s = make_state([(MAKE, NEW_INSTANCE)], [("com.example.App", "make", "java.lang.Class", "newInstance")])
    ok, fb = check_symptoms(s)
    assert not ok and s.spec_too_broad_feedback is None, "nothing reproduces: the hypothesis, not the spec"
    assert "is not missed by WALA/0-CFA" in fb


if __name__ == "__main__":
    test_all_missed()
    test_spec_too_broad()
    test_nothing_missed()
    print("reproduction: all tests passed")
