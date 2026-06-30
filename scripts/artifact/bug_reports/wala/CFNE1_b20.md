## Title

Missing call graph edges for implicitly created JVM exceptions (ClassCastException from checkcast)

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool. 

## Description

### Summary

WALA models JVM runtime exceptions (e.g., `ClassCastException` triggered by a failed `checkcast`) by recording exception types in the call graph and creating corresponding instance keys for points-to analysis. However, it does **not generate call graph edges to exception constructors (`<init>`)** for these implicitly created exception objects.

As a result, exception object instantiation exists in the heap model but is absent from the call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that triggers a runtime `ClassCastException` via a `checkcast` instruction.


### How to reproduce

Run WALA call graph on the attached project using the provided Maven setup. WALA is run with 0-cfa algorithm, full reflection support and default configuration. version: 1.5.7

The script to run the tool using JCG interface (judge link). 

```bash
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* WALA correctly identifies `ClassCastException` as a potential exception type
* WALA creates an `InstanceKey` for the exception object in PEI handling
* However, the call graph contains **no edge to**:

  ```
  java/lang/ClassCastException.<init>()V
  ```

---

## Expected Behavior

For JVM exceptions implicitly created at runtime (e.g., `checkcast`, `arraystore`, division by zero, etc.):

* The analysis should model the allocation of the exception object
* A call graph edge should exist from the implicit exception creation site to:

  ```json
  {"caller": "com.example.App.main([Ljava/lang/String;)V", "callee": "java.lang.ClassCastException.<init>(Ljava/lang/String;)V"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `JavaLanguage#SSACheckCastInstruction#getExceptionTypes()` returns only exception *types*, not constructors
* `SSAPropagationCallGraphBuilder#getInstanceKeyForPEI(...)` creates an `InstanceKey` for the exception type
* However, no synthetic call edge is added to the exception constructor

Relevant locations:

* `SSAPropagationCallGraphBuilder.java` (PEI handling)
* `ClassBasedInstanceKeys.java` (exception instance creation)
* `JavaLanguage.java` (checkcast exception modeling)

---

## Impact

If the omission of this edge is unintended, it may affect analyses that depend on accurate modeling of exceptional control flow. In particular, a failed cast operation can lead to the creation of a ClassCastException, and the corresponding constructor invocation may need to be represented in the call graph. Accounting for such behavior could benefit downstream analyses, such as those aimed at identifying vulnerabilities or reasoning about exception propagation.
