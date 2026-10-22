"""
Tests for the [UNSOUNDNESS] warning checks (issue #4): the pure checks, and
stage_check_trace with the annotated-tool run stubbed out.

    cd agentic_workflow && python tests/warning_checks_test.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline.stages.annotation as A
from pipeline.state import PipelineState
from pipeline.warning_checks import (
    check_description, check_negative, check_selectivity, warning_callers, warning_descriptions, warning_lines,
)

LIMITATION = "Class.forName with non-constant name not resolved"


def line(caller: str) -> str:
    return f"[info] [UNSOUNDNESS] {LIMITATION} | caller={caller} | site=invokevirtual@12 | line=7 | pc=12"


def make_test_case(root: Path, name: str, pkg: str = "com.example") -> Path:
    d = root / name
    src = d / "src/main/java" / pkg.replace(".", "/")
    src.mkdir(parents=True)
    (src / "App.java").write_text(f"package {pkg}; public class App {{}}")
    (d / "staticcg").mkdir()
    return d


def make_cg(root: Path, n_methods: int) -> Path:
    methods = [{"method": {"declaringClass": f"Ljava/util/C{i};", "name": f"m{i}"}} for i in range(n_methods - 1)]
    methods.append({"method": {"declaringClass": "Lcom/example/App;", "name": "main"}})
    p = root / "cg.json"
    p.write_text(json.dumps({"reachableMethods": methods}))
    return p


def test_parsing():
    lines = warning_lines("noise\n" + line("com.example.App.main([Ljava/lang/String;)") + "\n" + line("a.B.c()") + "\n"
                          + line("com.example.App.main([Ljava/lang/String;)"))
    assert len(lines) == 3
    assert warning_callers(lines) == ["com.example.App.main([Ljava/lang/String;)", "a.B.c()"]
    assert warning_callers(["[UNSOUNDNESS] p | caller=x.Y.z()"]) == ["x.Y.z()"]  # no site= part


def test_description():
    good = [line("com.example.App.main()")]
    assert warning_descriptions(good) == [LIMITATION]
    assert check_description(good, 60) == (True, "")
    for bad in ("[UNSOUNDNESS] <description> | caller=a.B.c() | site=s",
                "[UNSOUNDNESS] reflection_indirect_instantiation pattern_class | caller=a.B.c() | site=s",
                "[UNSOUNDNESS]  | caller=a.B.c() | site=s",
                "[UNSOUNDNESS] " + "the tool does not resolve " * 4 + "| caller=a.B.c() | site=s"):
        ok, fb = check_description([bad], 60)
        assert not ok and "at most 60 characters" in fb, bad


def test_selectivity(root: Path):
    cg = make_cg(root, 1000)
    precise = [line("com.example.App.main()"), line("java.util.ServiceLoader.load()")]
    assert check_selectivity(precise, str(cg), 0.05) == (True, "")
    generic = [line(f"java.util.C{i}.m{i}()") for i in range(300)]
    ok, fb = check_selectivity(generic, str(cg), 0.05)
    assert not ok and "300 distinct callers" in fb and "30%" in fb
    assert check_selectivity(generic, None, 0.05)[0]  # no static CG → cannot judge, pass


def test_negative(root: Path):
    nr = make_test_case(root, "non_reproducing")
    assert check_negative([line("java.lang.Class.forName(Ljava/lang/String;)")], nr) == (True, "")
    ok, fb = check_negative([line("com.example.App.main([Ljava/lang/String;)")], nr)
    assert not ok and "com.example.App.main" in fb
    assert check_negative([line("com.examples.Other.x()")], nr)[0]  # prefix of package name is not a match


def run_trace_check(root: Path, repro_lines, non_repro_lines, n_methods=1000):
    repro = make_test_case(root, "reproducing")
    non_repro = make_test_case(root, "non_reproducing")
    (repro / "staticcg/log.txt").write_text("\n".join(repro_lines))
    oracle = repro / "oracle.json"
    oracle.write_text(json.dumps({"RC:com.example.App.main()V@3": {
        "method": "com.example.App.main([Ljava/lang/String;)V", "symptoms": [{"caller": "x", "callee": "y"}]}}))
    s = PipelineState(program_name="p", tool_name="Soot", algorithm="SPARK", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=".", boundary={}, output_dir=root)
    s.reproducing_test_case_path, s.non_reproducing_test_case_path = str(repro), str(non_repro)
    s.oracle_path, s.staticcg_path = str(oracle), str(make_cg(root, n_methods))
    (root / "boundaries.json").write_text(json.dumps({"boundaries": [
        {"caller": {"declaringClass": "Lcom/example/App;", "name": "main"},
         "missedCallee": {"declaringClass": "Lcom/example/App$Impl;", "name": "run"}, "line": 7, "pc": 12}]}))
    s.boundary_path = str(root / "boundaries.json")
    runs = []

    def fake_run(state, project="reproducing", test_case_path=None):
        runs.append(project)
        (Path(test_case_path) / "staticcg/log.txt").write_text("\n".join(non_repro_lines))
    A.run_annotated_sa = fake_run
    A.build_agent_options = lambda state: None
    ok, fb = asyncio.run(A.stage_check_trace(s))
    return ok, fb, runs, s


def test_stage(root_factory):
    main = "com.example.App.main([Ljava/lang/String;)"
    ok, fb, runs, s = run_trace_check(root_factory(), [line(main)], [line("java.lang.Class.forName()")])
    assert ok and runs == ["non_reproducing"] and s.negative_trace_path, (ok, fb)
    assert s.warning_coverage["explained"] == 1 and s.warning_coverage["boundary_only"] == 0  # recorded (#14)

    no_location = f"[UNSOUNDNESS] {LIMITATION} | caller={main} | site=s"
    ok, fb, runs, _ = run_trace_check(root_factory(), [no_location], [])
    assert not ok and "Location check failed" in fb and runs == []

    ok, fb, runs, _ = run_trace_check(root_factory(), [line(main)] + [line(f"java.util.C{i}.m{i}()") for i in range(400)], [])
    assert not ok and "Selectivity" in fb and runs == []  # selectivity fails before the second tool run

    ok, fb, runs, s = run_trace_check(root_factory(), [line(main)], [line(main)])
    assert not ok and "Negative check failed" in fb
    feedback = A._build_annotation_feedback(s.__class__(**{**s.__dict__, "trace_feedback": [fb]}))
    assert "NON-reproducing" in feedback  # the retry prompt shows the offending lines

    long_desc = f"[UNSOUNDNESS] {'x' * 80} | caller={main} | site=s"
    ok, fb, runs, _ = run_trace_check(root_factory(), [long_desc], [])
    assert not ok and "Description check failed" in fb and runs == []

    ok, fb, runs, _ = run_trace_check(root_factory(), [line("other.Pkg.m()")], [])
    assert not ok and "none with caller= naming the root-cause method" in fb and "other.Pkg.m()" in fb  # the original check still applies


if __name__ == "__main__":
    tmp = lambda: Path(tempfile.mkdtemp())
    test_parsing()
    test_description()
    test_selectivity(tmp())
    test_negative(tmp())
    test_stage(tmp)
    print("warning checks: all tests passed")
