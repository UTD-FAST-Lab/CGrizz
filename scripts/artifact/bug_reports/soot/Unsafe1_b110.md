## Title

Missing call graph edges due to absent modeling of `sun.misc.Unsafe.compareAndSwapObject` heap effects

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

---

## Description

### Summary

SPARK (via Soot’s call graph construction pipeline) does not model the heap effect of `sun.misc.Unsafe.compareAndSwapObject`. As a result, object writes performed through CAS are not reflected in the points-to graph.

Consequently, interface or virtual calls whose receiver is initialized via CAS remain unresolved, and expected call graph edges are missing.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that triggers an unsafe heap update via `sun.misc.Unsafe.compareAndSwapObject`.

### How to reproduce

Run Soot/SPARK call graph construction on the provided project using a standard configuration (default SPARK setup or 0-CFA equivalent used in the evaluation pipeline).

Example execution:

```bash
python analysis_interface.py --framework SPARK --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* `Unsafe.compareAndSwapObject` is invoked in the program
* No corresponding points-to propagation is produced for the written field
* The field `objectVar` remains empty in SPARK’s points-to graph
* As a result, the call:

```
o.objectVar.targetMethod()
```

is unresolved

* No call graph edge is generated to:

```
cats.unsafe1.unsafe.UnsafeTarget.targetMethod()
```

---

## Expected Behavior

A sound heap model for `Unsafe.compareAndSwapObject` should:

* Propagate the stored object (`UnsafeTarget`) into the target field
* Update the points-to set of the field dereference (`objectVar`)
* Enable resolution of virtual/interface dispatch at the call site

Expected missing edge:

```json
{
  "caller": "cats.unsafe1.unsafe.Demo.main([Ljava/lang/String;)",
  "callee": "cats.unsafe1.unsafe.UnsafeTarget.targetMethod()"
}
```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `SunMiscUnsafeNative` only models a subset of Unsafe operations
* `compareAndSwapObject` falls back to a default case with **no heap effect modeling**
* `MethodNodeFactory.handleStmt` does not introduce any PAG edges for CAS writes
* Consequently, no points-to fact is generated for the updated field

Relevant locations:

* `SunMiscUnsafeNative.java`
* `NativeMethodClass.java`
* `MethodNodeFactory.java` (PAG construction path)

---

## Impact

This missing model causes unsoundness in any analysis relying on heap precision for:

* concurrent / atomic updates
* lock-free data structures
* dynamically initialized object graphs

In particular, CAS-based field updates are invisible to the points-to analysis, which can lead to missing virtual dispatch edges and incomplete call graphs in realistic concurrent Java programs.
