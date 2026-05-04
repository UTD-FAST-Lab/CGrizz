---
name: root-cause-annotator
description: Adds a single targeted log to the SA tool's source code at the exact point where an unsoundness root cause manifests
---

# Root Cause Annotator

You are given an unsoundness report and a static analysis tool source directory. Add exactly one `[UNSOUNDNESS]` log statement to the tool's source code at the point where the root cause occurs at runtime.

## Input

You will receive:
- **Unsoundness report JSON** (path) — specifically the `toolCodeEvidence` field with `relevantFiles` and `codeSnippet`
- **SA tool source directory** — to annotate

## Step 1: Identify the single injection point

Read `toolLimitation.toolCodeEvidence`:
- `relevantFiles` lists files and line numbers where the root cause chain appears
- `codeSnippet` shows the key decision logic

**Choose the innermost location** — the deepest point in the call chain where the tool definitively "gives up" (returns null, returns empty, skips the callee). This is typically where a guard condition fails (e.g. `if (symbolTable.isStringConstant(use))` returns false — inject in the else path). Do **not** inject at every file in `relevantFiles`. One location only.

## Step 2: Check for existing logs

Before modifying, search the target file for `[UNSOUNDNESS]` within the same method or block:

- **Same reason** (same `pattern_class` or equivalent `toolLimitation.reason`): skip — do not add a duplicate. The root cause for this boundary is already captured.
- **Different reason**: update the existing log to emit both reasons, e.g.:
  ```java
  System.out.println("[UNSOUNDNESS] reflection_indirect_instantiation | virtual_dispatch | caller=" + ...);
  ```

## Step 3: Add the log

Add exactly one print statement at the chosen injection point, using this exact format:

```java
System.out.println("[UNSOUNDNESS] " + "<pattern_class>" + " | caller=" + <caller_expr> + " | site=" + <site_expr>);
```

Where:
- `<pattern_class>` — string literal from the report's `pattern_class` field (e.g. `"reflection_indirect_instantiation"`)
- `<caller_expr>` — the runtime expression for the method being analyzed, using variables available in scope (e.g. `caller.getMethod()`, `caller.toString()`)
- `<site_expr>` — the call site reference, using variables in scope (e.g. `site`, `site.toString()`, `callSite`)

If the exact caller/site variables are not in scope at the injection point, walk up the call stack to the nearest enclosing method that has them and inject there instead. If that is not possible, just add the log with the pattern_class. 

You may add surrounding logic (e.g. an `if` or `else` branch, a null-check, a guard condition) to ensure the log fires only when the root cause is actually occurring — not on every invocation.

Note that you can add the log statement in any language that the tool is written on. For example, Scala or Java. 

**Example 1:**
```java
// Before the method returns null due to non-constant string
if (symbolTable.isStringConstant(use)) {
    // tool resolves class — no issue
} else {
    System.out.println("[UNSOUNDNESS] reflection_indirect_instantiation | caller=" + caller.getMethod() + " | site=" + site);
    // fall through to return null
}
```

