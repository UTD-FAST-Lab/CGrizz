## Title

Missing call graph edges after heap updates performed through `sun.misc.Unsafe.putObject`

---

**Note**: This is the artifact of a research project designed to help static analysis developers reason about and localize the unsoundness issues of their tool.

## Description

### Summary

WALA does not model the heap-modifying side effects of `sun.misc.Unsafe.putObject(...)`, `putOrderedObject()`, `putObjectVolatile()` or `getObject(Object, long)` and `getObjectVolatile()`. As a result, objects written to fields through this API are not propagated through pointer analysis. When those fields are later used as receivers of virtual or interface calls, WALA cannot determine their concrete types, leading to missing call graph edges.

Consequently, method invocations performed on objects stored via `Unsafe.putObject(...)` may be omitted from the generated call graph.

---

## Minimal Reproduction

I have attached a Maven-based minimal project that uses `sun.misc.Unsafe.putObject(...)` to update a field of interface type and subsequently invokes a method through that field reference.

### How to reproduce

Run WALA call graph on the attached project using the provided Maven setup. WALA is run with 0-cfa algorithm, full reflection support and default configuration. version: 1.5.7

The script to run the tool using JCG interface (judge link).

```bash
python analysis_interface.py --framework WALA --algorithm 0-CFA --project reproducing --type static --reproducing_test_case_path [path to test case directory with jcg.conf] --boundary_id 1
```

---

## Observed Behavior

* WALA successfully models the retrieval of the `Unsafe` instance and the computation of field offsets required by `putObject(...)`
* However, the heap update performed by `Unsafe.putObject(...)` is not reflected in the pointer analysis
* The field updated through the `putObject(...)` operation therefore does not point to the newly assigned object
* When the field is subsequently used as the receiver of an interface invocation, WALA cannot determine the concrete implementation type
* Consequently, the call graph contains **no edge to**:

  ```
  com.example.ConcreteImpl.method()V
  ```

---

## Expected Behavior

For programs that modify object fields through `Unsafe.putObject(...)` and subsequently invoke methods through those fields:

* The analysis should model the heap effects of the `putObject(...)` operation
* The field updated by `putObject(...)` should be associated with the assigned object in the pointer analysis
* A call graph edge should exist for the invoked method, for example:

  ```json
  {"caller": "com.example.Main.main([Ljava/lang/String;)V", "callee": "com.example.ConcreteImpl.method()V"}
  ```

---

## Notes on Implementation (inspected behavior)

From code inspection:

* WALA uses native method summaries to model the behavior of selected `sun.misc.Unsafe` operations

* The available summaries include several primitive field update methods such as:

  * `putBoolean(...)`
  * `putByte(...)`
  * `putChar(...)`
  * `putShort(...)`
  * `putInt(...)`
  * `putLong(...)`
  * `putFloat(...)`
  * `putDouble(...)`

* However, there does not appear to be a corresponding summary for:

  * `putObject(Ljava/lang/Object;JLjava/lang/Object;)V`

* When WALA encounters a call to `Unsafe.putObject(...)`, the heap-modifying side effects of the operation are therefore not incorporated into pointer analysis

* As a result, fields updated through this API appear unchanged in the SSA representation, preventing subsequent receiver type resolution

Relevant locations:

* `natives.xml`
* `SSAPropagationCallGraphBuilder.java`

---

## Impact

If the omission of this edge is unintended, it may affect analyses that rely on sound modeling of low-level heap updates performed through `sun.misc.Unsafe`. Direct object writes through `Unsafe.putObject(...)` are used in framework internals, serialization libraries, concurrent utilities, and performance-sensitive code that bypasses standard field assignment semantics. Missing these heap modifications could reduce call graph completeness and impact downstream analyses that depend on accurate interprocedural information, such as vulnerability detection, impact analysis, and reasoning about application behavior in the presence of unsafe memory manipulation.
