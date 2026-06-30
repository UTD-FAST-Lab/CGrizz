## Title

Missing call graph edges for SPI-based factory instantiation via `DocumentBuilderFactory.newInstance()`

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

WALA does not model Java Service Provider Interface (SPI) based factory instantiation performed through `DocumentBuilderFactory.newInstance()`. As a result, the analysis cannot determine the concrete implementation type returned by the factory method. When subsequent virtual calls are invoked on the returned factory object, WALA fails to resolve the target method, leading to missing call graph edges.

This limitation affects factory-based instantiation patterns where the concrete class is determined dynamically via system properties or SPI configuration.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that uses `DocumentBuilderFactory.newInstance()` to obtain a factory instance and invokes `newDocumentBuilder()` on it.

### How to reproduce

Run WALA call graph on the attached project using the provided Maven setup. WALA is run with 0-cfa algorithm, full reflection support and default configuration. version: 1.5.7

The script to run the tool using JCG interface (judge link).

```bash id="t9p4xa"
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* WALA does not model the SPI-based instantiation performed by `DocumentBuilderFactory.newInstance()`
* The returned factory object (`dbf`) is not associated with a precise concrete type in pointer analysis
* As a result, the receiver of the subsequent virtual call is imprecise or empty
* During call graph construction, WALA cannot resolve dispatch targets for:

  ```
  dbf.newDocumentBuilder()
  ```
* Consequently, the call graph contains **no edge to**:

  ```
  com/sun/org/apache/xerces/internal/jaxp/DocumentBuilderFactoryImpl.newDocumentBuilder()Ljavax/xml/parsers/DocumentBuilder;
  ```

---

## Expected Behavior

For programs using SPI-based factory instantiation:

* The analysis should model the factory resolution performed by `DocumentBuilderFactory.newInstance()`
* The returned factory instance should be associated with its concrete implementation type (e.g., Xerces `DocumentBuilderFactoryImpl`)
* Virtual dispatch on the factory object should resolve correctly
* A call graph edge should exist for the resolved implementation method, for example:

  ```json
      {"caller": "com.example.XMLParserDemo.parseDocument()V", "callee": "com.sun.org.apache.xerces.internal.jaxp.DocumentBuilderFactoryImpl.newDocumentBuilder()Ljavax/xml/parsers/DocumentBuilder;"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* `DocumentBuilderFactory.newInstance()` relies on `FactoryFinder.find()` to resolve implementations using:

  * System properties
  * `META-INF/services`
  * Default fallback implementation (e.g., Xerces)
* WALA does not include a native model for `DocumentBuilderFactory.newInstance()` in `natives.xml`
* As a result, no allocation site or factory summary is produced for the returned object
* The subsequent virtual call resolution in `ClassHierarchyMethodTargetSelector` depends on receiver type information, which is missing when the factory object is imprecisely modeled
* When the receiver points-to set is empty or imprecise, dispatch resolution fails, and no call edges are generated

Relevant locations:

* `natives.xml` (missing SPI factory model)
* `ClassHierarchyMethodTargetSelector.java`
* `DefaultSSAInterpreter.java` (factory handling limitations)
* `SSAPropagationCallGraphBuilder.java` (dispatch resolution logic)

---

## Impact

If the omission is unintended, it may affect analyses that rely on accurate modeling of common Java factory and SPI mechanisms. These patterns are widely used in XML parsing, logging frameworks, dependency injection systems, and plugin architectures. Failure to model SPI-based instantiation can lead to incomplete call graphs and missing edges for dynamically selected implementations, reducing the precision of downstream analyses such as dependency tracking, vulnerability analysis, and framework behavior understanding.
