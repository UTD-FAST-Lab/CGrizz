# Unsoundness markers for OPAL (type-based points-to, `TypeBasedPointsToCallGraphKey`)

The annotated OPAL copy contains `org.opalj.br.fpcf.properties.pointsto.Unsound`. Use it when the
root cause makes OPAL **lose a value that later reaches calls**: an unmodelled or body-less
method's result, a reflective allocation it cannot resolve, a field the JVM sets, and so on. Then
OPAL reports every call that value reaches, as one line per call:

```
[UNSOUNDNESS-EDGE] <description> | caller=pkg.Cls.m(params) | site=<callee> | line=<n> | pc=<n> | rc=<id>
```

`pc` is the call's bytecode pc and `line` is `body.lineNumber(pc)`, as JCG's OPAL adapter records
boundaries.

## How

OPAL's points-to sets are sets of types. At the give-up point, next to your `[UNSOUNDNESS]`
root-cause warning, add the marker type to the points-to set of the lost value. Inside a
points-to analysis (`PointsToAnalysisBase` and its subclasses) that is:

```scala
state.includeSharedPointsToSet(
    <entity>,                     // e.g. the definition site of the lost value (defSiteObject)
    createPointsToSet(pc, <context>,
        org.opalj.br.fpcf.properties.pointsto.Unsound.markerType("<id>", "<description>"),
        isConstant = false),
    PointsToSetLike.noFilter
)
```

- `<id>`: a short stable id for this root cause; `<description>`: the same at most 60-character
  description as your `[UNSOUNDNESS]` warning.

Guard it exactly like the warning: only for this root cause. The marker type is reserved
(`cgrizz/unsound/Marker_<id>`). `TypeBasedPointsToSet`'s filters and
`TypeIterator.isPossibleType` let it through, and `CallGraphAnalysis.handleCall` never resolves a
target for it (not even an "unknown library call"). **The call graph does not change**, and the
pipeline checks that.

## Example (OPAL O22/O23: the synthetic return value of a body-less method is dropped for abstract types)

In `ConfiguredMethodsPointsToAnalysis.handleGet`, before
`if (!filterNonInstantiableTypes || canBeInstantiated(theInstantiatedType))`:

```scala
if (filterNonInstantiableTypes && !canBeInstantiated(theInstantiatedType)) {
    println("[UNSOUNDNESS] abstract return type not modelled | caller=... | site=... | line=... | pc=...")
    state.includeSharedPointsToSet(
        defSiteObject,
        createPointsToSet(pc, allocationContext,
            org.opalj.br.fpcf.properties.pointsto.Unsound.markerType("abstractReturn", "abstract return type not modelled"),
            isConstant = false),
        PointsToSetLike.noFilter)
}
```

On `test/cases/abstract_return`, OPAL then warns at the calls on `getClassLoader()`'s and
`openConnection()`'s results, gives no warning for `String.valueOf(...).length()` (a concrete type
it does model), and computes the same call graph. Check with `tool_markers/run_test.sh opal`.

## When not to use it

If the edge is missed at the root-cause instruction itself (implicit exceptions, linkage errors,
`Thread.start`), there is no lost value to follow. The `[UNSOUNDNESS]` warning with that
instruction's `line=` / `pc=` already is the per-edge warning.
