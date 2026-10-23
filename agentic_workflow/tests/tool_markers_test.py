"""
Tests for tool unsoundness markers (issue #14, step 2): the installer, the
non-interference and coverage checks, and stage 8 with [UNSOUNDNESS-EDGE] lines.
The real-tool tests are tool_markers/run_test.sh <soot|wala|opal>.

    cd agentic_workflow && python tests/tool_markers_test.py
"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pipeline.stages.annotation as A
from pipeline import tool_markers
from pipeline.edge_mapping import map_files
from pipeline.state import PipelineState
from pipeline.warning_checks import check_coverage, check_noninterference, warning_records

SOOT_EDITS = list(dict.fromkeys(e["file"] for e in tool_markers.spec("Soot")["edits"]))
CALLGRAPH, BUILDER = "src/main/java/soot/jimple/spark/solver/OnFlyCallGraph.java", \
    "src/main/java/soot/jimple/toolkits/callgraph/OnFlyCallGraphBuilder.java"


def fake_soot(root: Path) -> Path:
    """A tool tree with just the files the Soot module edits, taken from the module's anchors."""
    tool = root / "soot"
    anchors: dict[str, str] = {}
    for e in tool_markers.spec("Soot")["edits"]:
        anchors[e["file"]] = anchors.get(e["file"], "") + e["anchor"]
    for f, text in anchors.items():
        p = tool / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("class X {\n" + text + "}\n")
    return tool


def test_install():
    tool = fake_soot(Path(tempfile.mkdtemp()))
    changed = tool_markers.install("Soot", tool)
    assert (tool / "src/main/java/soot/jimple/spark/unsound/Unsound.java").exists()
    assert set(SOOT_EDITS) <= set(changed) and len(changed) == len(set(changed))
    once = {f: (tool / f).read_text() for f in SOOT_EDITS}
    tool_markers.install("Soot", tool)                                   # idempotent
    assert {f: (tool / f).read_text() for f in SOOT_EDITS} == once
    assert "Unsound.onReceiver" in once[CALLGRAPH] and "cgrizzSitesOf" in once[BUILDER]
    assert "CGrizzMarkerField" in once["src/main/java/soot/jimple/spark/pag/PAG.java"]

    broken = fake_soot(Path(tempfile.mkdtemp()))
    (broken / CALLGRAPH).write_text("class X {}\n")                  # a different tool version
    try:
        tool_markers.install("Soot", broken)
        raise AssertionError("expected ToolMarkerError")
    except tool_markers.ToolMarkerError as e:
        assert "anchor found 0 times" in str(e)
    assert tool_markers.spec("Doop") is None and tool_markers.install("Doop", broken) == []


TOOLS = ["Soot", "WALA", "OPAL"]


def test_every_module_is_complete():
    for tool in TOOLS:
        spec = tool_markers.spec(tool)
        assert spec and spec["edits"], tool
        assert tool_markers.usage_file(tool) and tool_markers.usage_file(tool).exists(), f"{tool}: usage file missing"
        for f in spec["files"]:
            assert (tool_markers.MARKERS_DIR / tool.lower() / f).exists(), f"{tool}: {f} missing"


def test_install_on_pinned_tools():
    for tool in TOOLS:
        src = ROOT.parent / "tools" / tool.lower()
        edited = {e["file"] for e in tool_markers.spec(tool)["edits"]}
        if not all((src / f).exists() for f in edited):
            print(f"  (tools/{tool.lower()} not downloaded — skipped; scripts/setup/download_tools.py {tool.lower()})")
            continue
        copy = Path(tempfile.mkdtemp()) / tool.lower()
        for f in edited:
            (copy / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / f, copy / f)
        changed = tool_markers.install(tool, copy)                       # anchors match the pinned commit
        assert edited <= set(changed), tool
        before = {f: (copy / f).read_text() for f in edited}
        tool_markers.install(tool, copy)                                 # idempotent, incl. "replace" edits
        assert {f: (copy / f).read_text() for f in edited} == before, tool


def cg(path: Path, edges: list[tuple[str, str, int]]) -> str:
    methods = {}
    for caller, target, line in edges:
        cls, m = caller.rsplit(".", 1)
        rm = methods.setdefault(caller, {"method": {"declaringClass": f"L{cls.replace('.', '/')};", "name": m},
                                         "callSites": []})
        tcls, tm = target.rsplit(".", 1)
        rm["callSites"].append({"line": line, "pc": -1, "targets": [{"declaringClass": f"L{tcls.replace('.', '/')};",
                                                                      "name": tm}]})
    path.write_text(json.dumps({"reachableMethods": list(methods.values())}))
    return str(path)


def test_noninterference():
    d = Path(tempfile.mkdtemp())
    base = [("com.ex.App.main", "com.ex.Safe.run", 24)]
    assert check_noninterference(cg(d / "a.json", base), cg(d / "b.json", base)) == (True, "")
    ok, fb = check_noninterference(cg(d / "a.json", base), cg(d / "c.json", base + [("com.ex.App.main", "com.ex.X.run", 19)]))
    assert not ok and "1 edges added, 0 removed" in fb and "com.ex.X.run" in fb
    assert check_noninterference(None, cg(d / "b.json", base))[0], "no original saved → nothing to compare"


EDGE = "[UNSOUNDNESS-EDGE] Unsafe.getObject result not modelled | caller=com.ex.App.main(java.lang.String[]) | site=com.ex.Target.run | line={line} | pc=-1 | rc=unsafeGetObject"
RC = "[UNSOUNDNESS] Unsafe.getObject result not modelled | caller=com.ex.App.main(java.lang.String[]) | site=getObject | line=18 | pc=-1"


def test_case(root: Path) -> Path:
    tc = root / "reproducing"
    (tc / "src/main/java/com/ex").mkdir(parents=True)
    (tc / "src/main/java/com/ex/App.java").write_text("package com.ex; class App {}")
    (tc / "staticcg").mkdir()
    return tc


def boundaries(path: Path) -> str:
    def b(callee, line, caller="Lcom/ex/App;"):
        return {"caller": {"declaringClass": caller, "name": "main"},
                "missedCallee": {"declaringClass": "Lcom/ex/UnsafeTarget;", "name": callee}, "line": line, "pc": -1}
    path.write_text(json.dumps({"boundaries": [b("run", 19), b("run", 21), b("hashCode", 23),
                                               b("x", 5, caller="Ljava/util/HashMap;")]}))   # not the test case's code
    return str(path)


def test_edge_parsing_and_coverage():
    d = Path(tempfile.mkdtemp())
    tc, bpath = test_case(d), boundaries(d / "b.json")
    rec = warning_records([EDGE.format(line=19)])[0]
    assert rec["kind"] == "edge" and rec["rc"] == "unsafeGetObject" and rec["line"] == 19
    trace = "\n".join([RC] + [EDGE.format(line=n) for n in (19, 21, 23)])
    assert check_coverage(trace, bpath, tc) == (True, ""), "the JDK-caller boundary is not the test case's"
    ok, fb = check_coverage("\n".join([RC, EDGE.format(line=19)]), bpath, tc)
    assert not ok and "2 of the 3 missed edges" in fb and "@ line 21" in fb
    (d / "log.txt").write_text(trace)
    assert map_files([d / "log.txt"], bpath)["counts"]["explained"] == 3


def run_stage(trace_lines, original_edges, annotated_edges, markers=True):
    d = Path(tempfile.mkdtemp())
    tc = test_case(d)
    (tc / "staticcg/log.txt").write_text("\n".join(trace_lines))
    oracle = tc / "oracle.json"
    oracle.write_text(json.dumps({"RC:x": {"method": "com.ex.App.main([Ljava/lang/String;)V",
                                           "symptoms": [{"caller": "a", "callee": "b"}]}}))
    s = PipelineState(program_name="p", tool_name="Soot", algorithm="SPARK", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=".", boundary={}, output_dir=d)
    s.reproducing_test_case_path, s.oracle_path = str(tc), str(oracle)
    s.staticcg_path = cg(d / "annotated_cg.json", annotated_edges)
    cg(tc / "staticcg.original.json", original_edges)
    s.boundary_path = boundaries(d / "b.json")
    s.tool_markers_installed = markers
    A.build_agent_options = lambda st: None
    return asyncio.run(A.stage_check_trace(s)), s


def test_stage():
    base = [("com.ex.App.main", "com.ex.Safe.run", 24)] + [(f"java.util.C{i}.m", "java.lang.Object.f", i) for i in range(200)]
    full = [RC] + [EDGE.format(line=n) for n in (19, 21, 23)]
    (ok, fb), s = run_stage(full, base, base)
    assert ok, fb
    assert s.warning_coverage["explained"] == 3

    (ok, fb), _ = run_stage([RC, EDGE.format(line=19)], base, base)
    assert not ok and "Coverage check failed" in fb

    (ok, fb), _ = run_stage([RC, EDGE.format(line=19)], base, base, markers=False)
    assert ok, "coverage is only required with a marker module"

    (ok, fb), _ = run_stage(full, base, base + [("com.ex.App.main", "com.ex.UnsafeTarget.run", 19)])
    assert not ok and "Non-interference check failed" in fb


if __name__ == "__main__":
    test_install()
    test_every_module_is_complete()
    test_install_on_pinned_tools()
    test_noninterference()
    test_edge_parsing_and_coverage()
    test_stage()
    print("tool markers: all tests passed")
