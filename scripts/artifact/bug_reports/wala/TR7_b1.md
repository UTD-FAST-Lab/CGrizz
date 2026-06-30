## Title

Missing call graph edges after reflective field access using `Field.get()`

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

WALA does not model reflective field access APIs such as `Class.getDeclaredField(...)`, `Field.get(...)` and `Field.set(...)`. As a result, objects retrieved through reflective field reads are not propagated through pointer analysis. When such objects are subsequently used as receivers of virtual or interface calls, WALA is unable to resolve their concrete types, leading to missing call graph edges.

Consequently, method invocations performed on objects obtained via `Field.get(...)` may be omitted from the generated call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that retrieves an object through reflective field access using `Field.get(...)` and subsequently invokes a method on the retrieved object.

### How to reproduce

Run WALA call graph on the attached project using the provided Maven setup. WALA is run with 0-cfa algorithm, full reflection support and default configuration. version: 1.5.7

The script to run the tool using JCG interface (judge link).

```bash
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* WALA processes calls to `Class.getDeclaredField(...)` and `Field.get(...)` without modeling the object returned by the reflective field access
* The value obtained from `Field.get(...)` is therefore not associated with an appropriate points-to set
* When the retrieved object is subsequently used as the receiver of an interface invocation, WALA cannot determine the concrete implementation type
* Consequently, the call graph contains **no edge to**:

  ```
  cats/tr7/tr/CallTarget.target()V
  ```

---

## Expected Behavior

For programs that obtain objects via reflective field access and subsequently invoke methods on those objects:

* The analysis should model the object returned by `Field.get(...)`
* The retrieved value should be propagated through pointer analysis
* A call graph edge should exist for the invoked method, for example:

  ```json
  {"caller": "com.example.App.main([Ljava/lang/String;)V", "callee": "com.example.ConcreteService.process()V"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* WALA provides specialized reflection support for APIs such as `Method.invoke(...)`, `Constructor.newInstance(...)`, and several `Class` reflection methods
* However, there does not appear to be a corresponding interpreter for `Field.get(...)` or `Field.set(...)`
* The supported methods documented in `JavaLangClassContextInterpreter` include reflective method and constructor lookups, but not reflective field lookups
* Additionally, `TypeReference` contains definitions for reflective constructors and methods, but no analogous reference for `java.lang.reflect.Field`
* As a result, values obtained through reflective field access are not incorporated into subsequent receiver type resolution

Relevant locations:

* `ReflectionContextInterpreter.java`
* `JavaLangClassContextInterpreter.java`
* `TypeReference.java`
* `SSAPropagationCallGraphBuilder.java`

---

## Impact

If the omission of this edge is unintended, it may affect analyses that rely on sound modeling of reflective field accesses. Reflective field reads are commonly used by serialization libraries, dependency injection frameworks, testing frameworks, and legacy Java applications. Missing these field-induced data flows could reduce call graph completeness and impact downstream analyses that depend on accurate interprocedural information, such as vulnerability detection, impact analysis, and reasoning about application behavior under reflection.
