## Title

Missing call graph edges for implicitly created JVM exceptions (`ClassCastException` from failed `checkcast`)

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

OPAL does not model the implicit allocation and initialization of `java.lang.ClassCastException` objects that occur when a bytecode `checkcast` instruction fails at runtime. As a result, exception constructors triggered by failed type casts are not represented as explicit call graph edges.

When a cast fails (e.g., `checkcast` between unrelated reference types), the JVM implicitly creates and initializes a `ClassCastException` object. However, OPAL’s call graph construction treats `checkcast` as a non-invocation statement and does not translate it into a constructor call, leading to missing edges for exception creation.

Consequently, control-flow paths that involve failed casts do not include the corresponding `ClassCastException.<init>` invocation in the generated call graph.

---

## Minimal Reproduction

A minimal Maven-based project is attached where a method performs an unsafe downcast using `Class.newInstance()` / reflective class loading followed by an incompatible cast.

### How to reproduce

Run OPAL’s 0-CFA call graph construction on the provided project using the included analysis script (JCG interface setup).

Unzip the artifact. The relevant test case is in the `reproducing/` directory and the driver script is in the `interface/` directory.

```bash
python analysis_interface.py \
  --framework OPAL \
  --algorithm 0-CFA \
  --project reproducing \
  --type static \
  --reproducing_test_case_path ../reproducing \
  --boundary_id 1
```

The failing cast occurs in the main execution path of:

```
cats.cfne1.cfne.Demo.main(java.lang.String[])
```

---

## Observed Behavior

* OPAL processes the bytecode `checkcast` instruction but treats it as a non-call statement in the TAC representation

* The failing cast triggers runtime creation of a `ClassCastException`

* However, this exception creation is not modeled as a constructor invocation

* The call graph does not include an edge from the failing method to:

  ```
  java.lang.ClassCastException.<init>()
  ```

* The exception handling path is partially represented (via exception tables), but the exception object initialization itself is missing from the call graph

---

## Expected Behavior

For programs where a `checkcast` instruction may fail at runtime:

* The analysis should model implicit exception creation triggered by failed casts
* The JVM’s implicit invocation of `ClassCastException.<init>()` should be represented in the call graph
* A call graph edge should be emitted, e.g.:

```json
{
  "caller": "com.example.App.run()",
  "callee": "java.lang.ClassCastException.<init>()"
}
```

This would ensure that exception-related control-flow is correctly reflected in interprocedural analysis.

---

## Notes on Implementation (inspected behavior)

From inspection of OPAL’s call graph construction:

* Call graph edges are generated in `CallGraphAnalysis.processMethod` by iterating over TAC statements
* Only explicit invocation statements are handled:

  * `StaticFunctionCallStatement`
  * `VirtualMethodCall`
  * `NonVirtualMethodCall`
  * `Invokedynamic` variants
* The `Checkcast` TAC instruction is explicitly matched but not processed as a call site
* The corresponding branch effectively performs no action (`// nothing to do`)
* Therefore, no edge is created for exception construction triggered by a failing cast

Relevant implementation locations:

* `CallGraphAnalysis.scala` (statement dispatch loop, explicit invoke handling)
* `CallGraphAnalysis.scala` (Checkcast case handling around lines ~295–301)
* `org.opalj.br.instructions.CHECKCAST`
* TAC statement hierarchy (`Checkcast` vs invocation statements)

The core issue is that exception construction is semantically implied by the JVM but not encoded as an explicit TAC-level invocation, and OPAL does not lift this implicit behavior into the call graph.

---

## Impact

If this omission is unintended, it affects analyses that rely on complete modeling of exceptional control flow.

Failed casts are common in real-world Java programs (e.g., unsafe deserialization, reflection-heavy frameworks, legacy codebases). Missing the implicit `ClassCastException` constructor call can lead to:

* Incomplete interprocedural call graphs involving exception flows
* Loss of precision in exception-aware analyses (e.g., exception propagation, catch reachability)
* Reduced accuracy in security analyses that track control flow through runtime type errors
* Under-approximation of runtime behavior in frameworks that rely on casting for dynamic dispatch or deserialization

Overall, this reduces the fidelity of call graphs in scenarios where runtime type checks fail and exceptions are implicitly instantiated by the JVM.
