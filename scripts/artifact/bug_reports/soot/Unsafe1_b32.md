## Title

Missing call graph edges for heap updates via `sun.misc.Unsafe.compareAndSwapObject`

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

SPARK does not model the heap effects of `sun.misc.Unsafe.compareAndSwapObject`. Although some `Unsafe` methods are handled via native summaries, CAS-based field updates are not reflected in the pointer analysis. As a result, objects written into fields via compare-and-swap operations do not flow into the program’s points-to graph.

When these fields are later used as receivers of virtual or interface calls, SPARK cannot resolve their concrete types, leading to missing call graph edges.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that uses `Unsafe.compareAndSwapObject` to store a concrete object into a field, which is later used in an interface call.

### How to reproduce

Run SPARK call graph construction on the attached project using the provided Maven setup.

The script to run the tool using JCG interface (judge link).

```bash id="c8v1mx"
python analysis_interface.py --framework SOOT --algorithm SPARK --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* SPARK processes the call to `Unsafe.compareAndSwapObject`
* The native method handling for this API falls back to a default stub
* No pointer graph edges are generated for the field write performed by the CAS operation
* The field `objectVar` therefore remains with an empty points-to set
* At the interface call site, SPARK cannot resolve the receiver type
* Consequently, the call graph contains **no edge to**:

  ```
  cats.unsafe1.unsafe.UnsafeTarget.targetMethod()
  ```

---

## Expected Behavior

For programs using atomic or low-level memory manipulation via `Unsafe.compareAndSwapObject`:

* The analysis should model the heap update performed by the CAS operation
* The updated field should point to the newly installed object in the points-to graph
* Subsequent interface or virtual calls on that field should resolve correctly
* A call graph edge should exist, for example:

  ```json
  {
    "caller": "com.example.App.run()",
    "callee": "com.example.ConcreteImpl.execute()"
  }
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `SunMiscUnsafeNative` only provides modeling for a subset of `Unsafe` methods (e.g., `allocateInstance`)
* `compareAndSwapObject` is not explicitly modeled and falls through to a default handler
* The default handler (`defaultMethod`) does not introduce any pointer-analysis constraints
* In `MethodNodeFactory.handleStmt`, the invocation is processed but no PAG edges are created for CAS effects
* As a result, no heap update is recorded for the target field
* The points-to set of the field remains empty, preventing resolution at the subsequent call site

Relevant locations:

* `MethodNodeFactory.java`
* `SunMiscUnsafeNative.java`
* `NativeMethodClass.java`

---

## Impact

If the omission is unintended, it may affect analyses that rely on accurate modeling of low-level atomic operations. `compareAndSwapObject` is widely used in concurrent libraries, lock-free data structures, and JVM internals. Missing its heap effects leads to incomplete points-to information, which in turn causes missing call graph edges and reduced precision in interprocedural analysis, especially in concurrent or unsafe memory manipulation scenarios.
