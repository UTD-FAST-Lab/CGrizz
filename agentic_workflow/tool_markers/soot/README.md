# Unsoundness markers for Soot (SPARK)

The annotated Soot copy contains `soot.jimple.spark.unsound.Unsound`. Use it when the root
cause makes SPARK **lose a value that later reaches calls**: an unmodelled native return,
a reflective allocation it cannot resolve, a heap write it drops, and so on. Then SPARK
itself reports every call that value reaches, as one line per call:

```
[UNSOUNDNESS-EDGE] <description> | caller=pkg.Cls.m(params) | site=<callee> | line=<n> | pc=<n> | rc=<id>
```

## How

At the give-up point, next to your `[UNSOUNDNESS]` root-cause warning, add:

```java
soot.jimple.spark.unsound.Unsound.mark(<var>, <method>, "<id>", "<description>");
```

- `<var>`: the PAG variable that should have held the lost object. Native models
  (`soot.jimple.toolkits.pointer.nativemethods.*Native.simulateMethod`) give you `returnVar`
  (or `thisVar` / `params[i]`, e.g. the value an `Unsafe.put*` stores). In PAG building
  (`soot.jimple.spark.builder.*`, `soot.jimple.spark.pag.*`) use the `VarNode` / `FieldRefNode`
  of the lost value.
- `<method>`: the `SootMethod` whose code you are in (the native method or the method
  being processed); may be `null`.
- `<id>`: a short stable id for this root cause, e.g. `"unsafeGetObject"`.
- `<description>`: the same at most 60-character description as your `[UNSOUNDNESS]` warning.

Guard it exactly like the warning: only for this root cause. `mark` adds a marker object
(type `NullType`) to the variable's points-to set. SPARK propagates it like the lost object
and type filters let it through, but SPARK never uses it as a receiver, a string or an
argument array, and its fields hold nothing, so **the call graph does not change**
(`tool_markers/run_test.sh soot` checks that).

## Example (Soot S15: `Unsafe.getObject` falls through the no-op native model)

In `SunMiscUnsafeNative.simulateMethod`, before the default branch:

```java
if (subSignature.equals("java.lang.Object getObject(java.lang.Object,long)")) {
  System.out.println("[UNSOUNDNESS] Unsafe.getObject result not modelled | caller=... | site=... | line=... | pc=...");
  soot.jimple.spark.unsound.Unsound.mark(returnVar, method, "unsafeGetObject", "Unsafe.getObject result not modelled");
}
```

With the program in `test/cases/unsafe_flow`, SPARK then warns at every call on the result
of `getObject`: directly, through a helper's return value, and through `Object.hashCode`. It
gives no warning for the field read SPARK resolves normally, and the call graph is identical.

## When not to use it

If the edge is missed at the root-cause instruction itself (implicit exceptions, bridge
methods, a direct edge the tool drops), there is no lost value to follow. The `[UNSOUNDNESS]`
warning with that instruction's `line=` / `pc=` already is the per-edge warning.
