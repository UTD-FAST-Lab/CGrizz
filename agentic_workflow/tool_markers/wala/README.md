# Unsoundness markers for WALA

The annotated WALA copy contains `com.ibm.wala.ipa.callgraph.propagation.unsound.Unsound`. Use it
when the root cause makes WALA **lose a value that later reaches calls**: an unmodelled native's
result, a reflective allocation it cannot resolve, a dropped heap write, and so on. Then WALA
reports every call that value reaches, as one line per call:

```
[UNSOUNDNESS-EDGE] <description> | caller=pkg.Cls.m(params) | site=<callee> | line=<n> | pc=<n> | rc=<id>
```

`pc` is the call site's program counter and `line` is `method.getLineNumber(pc)`, exactly as
JCG's WALA adapter records boundaries.

## How

At the give-up point, next to your `[UNSOUNDNESS]` root-cause warning, add one of:

```java
// the lost value is local `valueNumber` of `node` (e.g. the def of an invoke instruction)
com.ibm.wala.ipa.callgraph.propagation.unsound.Unsound.mark(builder, node, valueNumber, "<id>", "<description>");
// any pointer key (instance field, static field, array contents, ...)
com.ibm.wala.ipa.callgraph.propagation.unsound.Unsound.mark(builder, pointerKey, "<id>", "<description>");
```

- `builder`: the `PropagationCallGraphBuilder` (`this` inside `SSAPropagationCallGraphBuilder`,
  `getBuilder()` inside its `ConstraintVisitor`).
- `<id>`: a short stable id for this root cause; `<description>`: the same at most 60-character
  description as your `[UNSOUNDNESS]` warning.

Guard it exactly like the warning: only for this root cause. The marker is an instance key of
type `java.lang.Object` that is registered with every class, so WALA's type filters (casts,
declared types) let it through, and `getTargetForCall` gives it no target. **The call graph does
not change** (`tool_markers/run_test.sh wala` checks that).

## Example (WALA W10: the result of the native `Unsafe.getObject` has no model)

In `SSAPropagationCallGraphBuilder.processResolvedCall`, before `processCallingConstraints(...)`:

```java
if (target.getMethod().isNative() && instruction.hasDef()
    && target.getMethod().getDeclaringClass().getName().toString().equals("Lsun/misc/Unsafe")
    && target.getMethod().getName().toString().equals("getObject")) {
  System.out.println("[UNSOUNDNESS] Unsafe.getObject result not modelled | caller=... | site=... | line=... | pc=...");
  com.ibm.wala.ipa.callgraph.propagation.unsound.Unsound.mark(
      this, caller, instruction.getDef(), "unsafeGetObject", "Unsafe.getObject result not modelled");
}
```

On `test/cases/unsafe_flow`, WALA then warns at every call on the result of `getObject`
(directly, through a helper's return value, and through `Object.hashCode`), and inside the JDK
where the JDK's own `getObject` results flow. It gives no warning where it resolves the receiver,
and the call graph is identical. Check with `tool_markers/run_test.sh wala`.

## When not to use it

If the edge is missed at the root-cause instruction itself (implicit exceptions, a failed method
resolution), there is no lost value to follow. The `[UNSOUNDNESS]` warning with that instruction's
`line=` / `pc=` already is the per-edge warning.
