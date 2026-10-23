"""
Symptoms "via": "reflection" — the member a reflective call reaches, which call graphs show
with a JDK reflection accessor as caller: matching in the verdict / dedup (is_symptom) and in
the reproduction check (method_reachable, reflective_edge_in_boundary).

    cd agentic_workflow && python tests/reflective_symptoms_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.edges import method_reachable, reflective_edge_in_boundary
from pipeline.oracle import is_symptom, symptom_edges

ORACLE = {"RC:com.ex.App.run()V@11": {"symptoms": [
    {"caller": "com.ex.App.run()V", "callee": "java.lang.Class.newInstance()Ljava/lang/Object;"},
    {"caller": "com.ex.App.run()V", "callee": "com.ex.App$Greeter.<init>()V", "via": "reflection"},
]}}


def test_is_symptom():
    edges = symptom_edges(ORACLE)
    assert is_symptom(("com.ex.App", "run", "java.lang.Class", "newInstance"), edges)
    assert is_symptom(("sun.reflect.NativeConstructorAccessorImpl", "newInstance0", "com.ex.App$Greeter", "<init>"), edges)
    assert is_symptom(("jdk.internal.reflect.GeneratedConstructorAccessor1", "newInstance", "com.ex.App$Greeter", "<init>"),
                      edges)
    assert not is_symptom(("java.util.HashMap", "get", "com.ex.App$Greeter", "<init>"), edges), "not an accessor"
    assert not is_symptom(("sun.reflect.NativeConstructorAccessorImpl", "newInstance0", "com.ex.App$Other", "<init>"),
                          edges), "another member"
    plain = symptom_edges({"RC:x": {"symptoms": [{"caller": "com.ex.App.run()V", "callee": "com.ex.App$Greeter.<init>()V"}]}})
    assert not is_symptom(("sun.reflect.NativeConstructorAccessorImpl", "newInstance0", "com.ex.App$Greeter", "<init>"),
                          plain), "only symptoms via reflection match an accessor caller"


def method(cls: str, name: str) -> dict:
    return {"declaringClass": f"L{cls.replace('.', '/')};", "name": name}


def test_reproduction_helpers():
    d = Path(tempfile.mkdtemp())
    cg = d / "cg.json"
    cg.write_text(json.dumps({"reachableMethods": [{"method": method("com.ex.App", "<init>"), "callSites": []},
                                                    {"method": method("com.ex.App", "run"), "callSites": []}]}))
    assert method_reachable("com.ex.App.run()V", str(cg)) is True
    assert method_reachable("com.ex.App$Greeter.<init>()V", str(cg)) is False, "exact class, not a prefix"
    assert method_reachable("com.ex.App.run()V", str(d / "missing.json")) is None

    b = d / "boundaries.json"
    b.write_text(json.dumps({"boundaries": [
        {"caller": method("sun.reflect.NativeMethodAccessorImpl", "invoke0"), "missedCallee": method("com.ex.T", "greet")},
        {"caller": method("com.ex.App", "run"), "missedCallee": method("com.ex.U", "greet")},
    ]}))
    assert reflective_edge_in_boundary(str(b), "com.ex.T.greet()V")
    assert not reflective_edge_in_boundary(str(b), "com.ex.U.greet()V"), "caller is not an accessor"


if __name__ == "__main__":
    test_is_symptom()
    test_reproduction_helpers()
    print("reflective symptoms: all tests passed")
