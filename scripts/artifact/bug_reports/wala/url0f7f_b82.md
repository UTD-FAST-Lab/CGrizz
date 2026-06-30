Missing JAXP Factory Modeling Prevents Resolution of DocumentBuilder Dispatch

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

WALA does not model the JAXP factory instantiation chain used by APIs such as `DocumentBuilderFactory.newInstance()` and `DocumentBuilderFactory.newDocumentBuilder()`. As a result, objects created through these factory methods are not propagated into pointer analysis.

When a program obtains a `DocumentBuilder` through the JAXP factory mechanism and subsequently invokes methods on it, WALA cannot determine the concrete implementation type because the factory-selected implementation is instantiated indirectly through service-loader and reflection mechanisms inside the JDK.

Consequently, virtual method invocations performed on factory-created `DocumentBuilder` instances may be omitted from the generated call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that obtains a `DocumentBuilder` using `DocumentBuilderFactory.newInstance().newDocumentBuilder()` and subsequently invokes a method on the returned object.

### How to reproduce

Run WALA call graph construction on the attached project using the provided Maven setup. WALA is run with the 0-CFA algorithm, full reflection support, and default configuration. Version: 1.5.7.

The script to run the tool using the JCG interface is attached.

Unzip the compressed file. The reproducing test case is in the `reproducing/` directory and the script to run the analysis is in the `interface/` directory.

```bash
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory ../reproducing] --boundary_id 1
```

---

## Observed Behavior

* WALA processes calls to `DocumentBuilderFactory.newInstance()` and `DocumentBuilderFactory.newDocumentBuilder()`
* However, the factory-selected implementation class is never instantiated within the analysis
* The concrete type `com.sun.org.apache.xerces.internal.jaxp.DocumentBuilderImpl` is therefore absent from the points-to graph
* When the returned object is subsequently used as the receiver of a virtual invocation, WALA cannot determine its concrete runtime type
* Consequently, the call graph contains **no edge to**:

  ```
  com.sun.org.apache.xerces.internal.jaxp.DocumentBuilderImpl.setErrorHandler(org.xml.sax.ErrorHandler)
  ```

---

## Expected Behavior

For programs that obtain objects through the JAXP factory mechanism and subsequently invoke methods on those objects:

* The analysis should model the implementation selected by `DocumentBuilderFactory.newInstance()`
* The resulting `DocumentBuilderImpl` object should be propagated through pointer analysis
* Virtual dispatch should resolve to the concrete implementation
* A call graph edge should exist, for example:

  ```json
  {
    "caller": "org.apache.log4j.xml.DOMConfigurator.doConfigure(org.apache.log4j.xml.DOMConfigurator$ParseAction, org.apache.log4j.spi.LoggerRepository)",
    "callee": "com.sun.org.apache.xerces.internal.jaxp.DocumentBuilderImpl.setErrorHandler(org.xml.sax.ErrorHandler)"
  }
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* WALA provides models for several reflection-related APIs, including `Class.newInstance()` and `Constructor.newInstance()`
* However, there does not appear to be a corresponding model for the JAXP factory instantiation chain
* The factory method `DocumentBuilderFactory.newInstance()` resolves the concrete implementation through service-loader and reflection mechanisms inside the JDK
* Because the implementation class is not explicitly allocated within the analysis, no `DocumentBuilderImpl` instance enters the points-to graph
* As a result, the receiver of `DocumentBuilder.setErrorHandler(...)` has an empty points-to set during call graph construction
* The dispatch operation therefore resolves no concrete targets

Relevant locations:

* `natives.xml`
* `SSAPropagationCallGraphBuilder.java`
* `Util.java`

In particular:

* `natives.xml` contains modeling for `Class.newInstance()` but no corresponding entry for:

  * `DocumentBuilderFactory.newInstance()`
  * `DocumentBuilderFactory.newDocumentBuilder()`
* `SSAPropagationCallGraphBuilder.DispatchOperator.cpa()` only dispatches over concrete receiver types present in the points-to set
* When the receiver points-to set is empty, no target methods are discovered

---

## Impact

If the omission of this edge is unintended, it may affect analyses that rely on sound modeling of factory-created JDK objects.

JAXP factories are widely used throughout Java applications and frameworks, including XML parsers, configuration systems, dependency injection frameworks, and application servers. Missing these factory-induced object flows can reduce call graph completeness and affect downstream analyses that depend on accurate interprocedural information, such as vulnerability detection, impact analysis, framework modeling, and reasoning about application behavior involving service-loader and reflection-based instantiation.
