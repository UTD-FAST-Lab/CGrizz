## Title

Missing call graph edges for implicitly created JVM exceptions (`ClassCastException` from failed `checkcast`)

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

SPARK constructs call graphs by processing explicit invoke expressions and a limited set of implicit targets (e.g., class initializers and selected reflection patterns). However, it does not model the implicit construction of JVM exceptions such as `ClassCastException` when a `checkcast` instruction fails at runtime.

As a result, constructor invocations for JVM-generated exceptions are absent from the generated call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that performs an invalid cast, causing the JVM to throw a `ClassCastException` during execution.

### How to reproduce

Run SPARK call graph construction on the attached project using the provided Maven setup.

The script to run the tool using JCG interface (judge link).

```bash
python analysis_interface.py --framework Soot --algorithm SPARK --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* SPARK correctly models the explicit calls in the program
* The `checkcast` instruction is translated into a Jimple `CastExpr`
* The `CastExpr` is treated as a normal assignment expression rather than a source of an implicit JVM exception
* Consequently, the call graph contains **no edge to**:

  ```
  java.lang.ClassCastException.<init>()
  ```

---

## Expected Behavior

For programs in which a `checkcast` instruction can fail at runtime:

* The analysis should account for the implicit construction of the corresponding exception object
* A call graph edge should exist from the enclosing method to the exception constructor, for example:

  ```json
  {"caller": "com.example.App.run()", "callee": "java.lang.ClassCastException.<init>()"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `OnFlyCallGraphBuilder` scans method bodies and processes statements containing explicit invoke expressions
* `checkcast` instructions are translated into Jimple `CastExpr` values that appear on the right-hand side of `AssignStmt`s
* During processing of `AssignStmt`s, SPARK handles initialization-related cases (e.g., `NewExpr`) but does not treat `CastExpr` as a potential source of implicit exception constructor invocations
* Since the JVM-generated allocation and invocation of `ClassCastException.<init>()` are not represented by explicit invoke instructions in the bytecode, no corresponding call graph edge is generated

Relevant locations:

* `OnFlyCallGraphBuilder.java`
* `MethodNodeFactory.java`

---

## Impact

If the omission of this edge is unintended, it may affect analyses that rely on accurate modeling of exceptional control flow. In particular, failed cast operations result in the JVM implicitly allocating and initializing a `ClassCastException` object. Representing these implicit constructor invocations in the call graph could improve downstream analyses that reason about exception propagation, object allocation, or exception-related vulnerabilities.
