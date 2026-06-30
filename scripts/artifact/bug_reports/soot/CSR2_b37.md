## Title

Missing call graph edges for implicitly created JVM exceptions (`ArrayIndexOutOfBoundsException` from array access)

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

SPARK constructs call graphs by processing explicit invoke expressions and a limited set of implicit targets (e.g., class initializers and selected reflection patterns). However, it does not model the implicit construction of JVM exceptions such as `ArrayIndexOutOfBoundsException` that are created when an array access fails at runtime.

As a result, constructor invocations for JVM-generated exceptions are absent from the generated call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that performs an out-of-bounds array access, causing the JVM to throw an `ArrayIndexOutOfBoundsException` during execution.

### How to reproduce

Run SPARK call graph construction on the attached project using the provided Maven setup.

The script to run the tool using JCG interface (judge link).

```bash id="m2c6rd"
python analysis_interface.py --framework SOOT --algorithm SPARK --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* SPARK correctly models the explicit calls in the program
* The array access is translated into an `ArrayRef` in Jimple
* The `ArrayRef` is treated as a normal array access rather than a source of an implicit JVM exception
* Consequently, the call graph contains **no edge to**:

  ```
  java.lang.ArrayIndexOutOfBoundsException.<init>()
  ```

---

## Expected Behavior

For programs in which an array access can fail at runtime:

* The analysis should account for the implicit construction of the corresponding exception object
* A call graph edge should exist from the enclosing method to the exception constructor, for example:

  ```json
  {"caller": "com.example.App.main([Ljava/lang/String;)", "callee": "java.lang.ArrayIndexOutOfBoundsException.<init>()"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `OnFlyCallGraphBuilder` scans method bodies and processes explicit invoke expressions, field references, and selected allocation-related statements
* Array accesses are represented in Jimple as `ArrayRef` expressions
* Although array access statements are visited during call graph construction, there is no logic that synthesizes constructor edges for the `ArrayIndexOutOfBoundsException` objects implicitly created by the JVM
* `UnitThrowAnalysis` identifies that array accesses may throw `ArrayIndexOutOfBoundsException`, but this information is used for exception analysis rather than call graph construction
* Consequently, no edge is added from the enclosing method to `ArrayIndexOutOfBoundsException.<init>()`

Relevant locations:

* `OnFlyCallGraphBuilder.java`
* `UnitThrowAnalysis.java`

---

## Impact

If the omission of this edge is unintended, it may affect analyses that rely on accurate modeling of exceptional control flow. Array bounds violations implicitly allocate and initialize `ArrayIndexOutOfBoundsException` objects at runtime. Representing these implicit constructor invocations in the call graph could improve downstream analyses that reason about exception propagation, object allocation, or exception-related vulnerabilities.
