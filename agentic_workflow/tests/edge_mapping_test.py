"""
Tests for matching [UNSOUNDNESS] warnings to boundaries (issue #14):
warning parsing, the location check and scripts/helpers/warning_coverage.py.

    cd agentic_workflow && python tests/edge_mapping_test.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.edge_mapping import boundary_records, map_files, map_warnings
from pipeline.warning_checks import check_location, warning_records


def w(caller, line, pc, desc="Unsafe.getObject result not modelled"):
    return f"[UNSOUNDNESS] {desc} | caller={caller} | site=x | line={line} | pc={pc}"


def b(cls, m, callee, line, pc):
    return {"caller": {"declaringClass": f"L{cls.replace('.', '/')};", "name": m},
            "missedCallee": {"declaringClass": f"L{callee.rsplit('.', 1)[0].replace('.', '/')};",
                             "name": callee.rsplit(".", 1)[1]}, "line": line, "pc": pc}


BOUNDARIES = [
    b("com.ex.App", "main", "com.ex.X.run", 7, 12),     # two missed callees at one instruction
    b("com.ex.App", "main", "com.ex.Y.run", 7, 12),
    b("com.ex.App", "main", "com.ex.Z.run", 20, 30),    # no warning
    b("com.ex.App", "load", "com.ex.W.go", 9, 50),      # matched by line (the warning has no pc)
]
WARNINGS = [
    w("com.ex.App.main([Ljava/lang/String;)", 7, 12),
    w("com.ex.App.main([Ljava/lang/String;)", 7, 12),   # the same instruction analysed twice
    w("com.ex.App.load()", 9, -1),
    w("com.ex.App.helper()", 40, 77),                   # no boundary
]


def test_records_and_location():
    r = warning_records(WARNINGS)
    assert r[0]["caller"].startswith("com.ex.App.main(") and r[0]["line"] == 7 and r[0]["pc"] == 12
    assert r[2]["pc"] is None and r[2]["line"] == 9
    assert check_location(WARNINGS) == (True, "")
    ok, fb = check_location(["[UNSOUNDNESS] d | caller=a.B.c() | site=s"])
    assert not ok and "no `line=` / `pc=` fields" in fb
    ok, fb = check_location([w("a.B.c()", -1, -1)])
    assert not ok and "both unknown" in fb


def test_mapping():
    m = map_warnings(warning_records(WARNINGS), boundary_records(BOUNDARIES))
    assert m["counts"] == {"boundaries": 4, "explained": 3, "boundary_only": 1,
                           "warned_instructions": 3, "warning_only_instructions": 1}, m["counts"]
    assert m["boundary_only"] == ["com.ex.App.main @ pc 30 → com.ex.Z.run"]
    assert m["warning_only"][0].startswith("com.ex.App.helper @ pc 77")
    assert all(e["description"] == "Unsafe.getObject result not modelled" for e in m["explained"])


def test_script():
    d = Path(tempfile.mkdtemp())
    (d / "log.txt").write_text("[info] noise\n" + "\n".join(WARNINGS))
    (d / "boundaries.json").write_text(json.dumps({"boundaries": BOUNDARIES}))
    assert map_files([d / "log.txt"], d / "boundaries.json")["counts"]["explained"] == 3
    script = ROOT.parent / "scripts/helpers/warning_coverage.py"
    out = subprocess.run([sys.executable, str(script), "--trace", str(d / "log.txt"), "--boundaries",
                          str(d / "boundaries.json")], capture_output=True, text=True, check=True).stdout
    assert "explained: 3" in out and "com.ex.Z.run" in out

    results = d / "results"
    tc = results / "prog/Soot/SPARK/b0/test_case/reproducing"
    (tc / "staticcg").mkdir(parents=True)
    (tc / "boundary/Soot/SPARK").mkdir(parents=True)
    (tc / "staticcg/log.txt").write_text("\n".join(WARNINGS))
    (tc / "boundary/Soot/SPARK/boundaries.json").write_text(json.dumps({"boundaries": BOUNDARIES}))
    (results / "prog/Soot/SPARK/b0/pipeline_result.json").write_text("{}")
    out = subprocess.run([sys.executable, str(script), "--results", str(results)],
                         capture_output=True, text=True, check=True).stdout
    assert "| prog | Soot/SPARK | b0 | 4 | 3 | 1 | 1 |" in out, out


if __name__ == "__main__":
    test_records_and_location()
    test_mapping()
    test_script()
    print("edge mapping: all tests passed")
