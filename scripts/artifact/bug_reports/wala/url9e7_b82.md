## Title

Missing call graph edges for library factory-based logger initialization (`LogManager.getLogger`)

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

WALA does not model the library factory method `LogManager.getLogger(...)`. As a result, no constraint is generated for the returned `Logger` instance, leaving its points-to set empty. When the logger is stored in a static field and later used in virtual dispatch calls (e.g., `LOG.info(...)`), WALA cannot resolve the receiver type, leading to missing call graph edges to concrete logging implementations.

This affects frameworks where interfaces are returned through library factory methods and subsequently used for dynamic dispatch.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that defines a `Logger` interface, an `AbstractLogger` implementation, and a `LogManager.getLogger(...)` factory method. The logger is stored in a static field and later used for method invocation.

### How to reproduce

Run WALA call graph on the attached project using the provided Maven setup. WALA is run with 0-cfa algorithm, full reflection support and default configuration. version: 1.5.7

The script to run the tool using JCG interface (judge link).

```bash id="m4q9la"
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* WALA encounters the call to `LogManager.getLogger(ConfigBassReal5.class)`
* No IR or method summary is available for this library factory method
* As a result, no pointer constraints are generated for the returned `Logger` object
* The static field `LOG` remains with an empty or imprecise points-to set
* When `LOG.info(String)` is invoked, WALA cannot resolve the receiver type
* Consequently, the call graph contains **no edge to**:

  ```
  com.example.AbstractLogger.info(Ljava/lang/String;)V
  ```

---

## Expected Behavior

For library factory methods returning interface types:

* The analysis should model the allocation or concrete binding performed inside `LogManager.getLogger(...)`
* The returned `Logger` instance should be associated with its concrete implementation type (e.g., `AbstractLogger`)
* Virtual dispatch on `LOG` should resolve correctly
* A call graph edge should exist for the resolved implementation method, for example:

  ```json
    {"caller": "com.example.App.main([Ljava/lang/String;)V", "callee": "com.example.AbstractLogger.info(Ljava/lang/String;)V"}, 
    {"caller": "com.example.App.processData()V", "callee": "com.example.AbstractLogger.info(Ljava/lang/String;)V"}, 
    {"caller": "com.example.App.main([Ljava/lang/String;)V", "callee": "com.example.AbstractLogger.info(Ljava/lang/String;)V"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* WALA attempts to obtain IR for library methods via `getCFAContextInterpreter().getIRView(node)`
* When no IR is available for a method, constraint generation is skipped entirely
* This prevents propagation of return values from factory methods like `LogManager.getLogger(...)`
* Consequently, the receiver object for subsequent virtual calls remains unresolved
* Virtual dispatch resolution in `ClassHierarchyMethodTargetSelector` depends on receiver points-to information, which is missing in this case

Relevant locations:

* `SSAPropagationCallGraphBuilder.java` (IR acquisition and constraint generation)
* `ClassHierarchyMethodTargetSelector.java` (dispatch resolution)
* `SSAPropagationCallGraphBuilder.java` (points-to propagation logic)

---

## Impact

If the omission is unintended, it may affect analyses that rely on correct modeling of widely used logging frameworks and similar factory-based APIs. Logging libraries such as Log4j are heavily used in enterprise Java systems, and their factory methods commonly return interface types backed by hidden concrete implementations. Missing modeling of these factory methods can lead to incomplete call graphs and missing edges for logging-related dispatches, affecting downstream analyses such as dependency tracking, debugging support tools, and security analyses involving logging behavior.
