---
name: root-cause-annotator
description: Adds a single targeted log to the SA tool's source code at the exact point where an unsoundness root cause manifests
---

# Root Cause Annotator

You are given an unsoundness report and a static analysis tool source directory. Add EXACTLY ONE `[UNSOUNDNESS]` log statement to the tool's source code at the point where the root cause occurs at runtime.

## Goal

This annotation should help both the developer of the tool and the users of the tool to localize the issue that is identified in the unsoundness report. It should not be generic. Examine the code base of the tool to understand the best place to add the annotation. 

## Input

You will receive:
- **Unsoundness report JSON** (path) — specifically the `toolCodeEvidence` field with `relevantFiles` and `codeSnippet`
- **SA tool source directory** — to annotate

## Step 1: Identify the single injection point

> **CRITICAL PRINCIPLE — inject in the CALLER's analysis context, never the callee's:**
>
> The log must be placed at a point where the SA tool is **analyzing the calling method** (the method that makes the missed call). At that point the SA tool is processing the specific call instruction that should produce the missed edge, and decides to omit or under-approximate it.
>
> - **Correct**: injecting in code that runs while the SA tool processes the `caller` method's instructions (e.g., inside a virtual-call resolver, a reflection handler, an API model, or a dispatch handler — all invoked in the context of analyzing `caller`).
> - **WRONG**: injecting in code that processes the callee itself.

Read `toolLimitation.toolCodeEvidence`:
- `relevantFiles` lists files and line numbers where the root cause chain appears
- `codeSnippet` shows the key decision logic

**Choose the innermost location** in the caller's analysis context — the deepest point in the call chain, still within the caller's analysis, where the tool definitively "gives up" (returns null, returns empty, skips the callee). This is typically where a guard condition fails (e.g. `if (symbolTable.isStringConstant(use))` returns false — inject in the else path). Do **not** inject at every file in `relevantFiles`. One location only.

If multiple files in `relevantFiles` are candidates, prefer the one that processes the **call instruction** in the caller's method body, not any entry that processes the callee method.

## Step 2: Add the log

Add exactly one print statement at the chosen injection point, using this exact format:

```java
System.out.println("[UNSOUNDNESS] " + "<pattern_class>" + " | caller=" + <caller_expr> + " | site=" + <site_expr>);
```

Where:
- `<pattern_class>` — string literal from the report's `pattern_class` field (e.g. `"reflection_indirect_instantiation"`)
- `<caller_expr>` — the runtime expression for the **calling method** (the method that makes the missed call, i.e., the `caller` field from the boundary entry). This is the method whose analysis is currently in progress at the injection point. It must NOT be the missed callee.
- `<site_expr>` — the call site reference, using variables in scope (e.g. `site`, `site.toString()`, `callSite`)

**Caller Format**
`caller_expr` should produce the method description of the **calling** method in this strict format: `package.classname.methodname(parameters)`. This format is later going to be parsed with a script, so make sure to keep the format exactly as this.
I use the following commands to get the classname and method name of the caller:

```python
ex_classname = expected_method.split("(")[0].rsplit(".", 1)[0] if "." in expected_method else None
ex_methodname = expected_method.split("(")[0].rsplit(".", 1)[1] if "." in expected_method else None
```

Example:
`java.nio.charset.CharsetEncoder.replaceWith([B)`

```
ex_classname = java.nio.charset.CharsetEncoder
ex_methodname = replaceWith
```

You should understand the format of the variable that contains the caller information at the injection site and reformat it to this exact format.

**Notes**
If the exact caller/site variables are not in scope at the injection point, walk up the call stack to the nearest enclosing method that has them and inject there instead.

You may add surrounding logic (e.g. an `if` or `else` branch, a null-check, a guard condition) to ensure the log fires only when the root cause is actually occurring — not on every invocation.

Note that you can add the log statement in any language that the tool is written on. For example, Scala or Java.

**Example 1 — Reflection (correct):**
```java
// In the caller's analysis, at the reflection handler that processes Class.forName:
if (symbolTable.isStringConstant(use)) {
    // tool resolves class — no issue
} else {
    // `caller` here refers to the method being analyzed (App.runParser, NOT the reflection target)
    System.out.println("[UNSOUNDNESS] reflection_indirect_instantiation | caller=" + caller.getMethod() + " | site=" + site);
    // fall through to return null
}
```

**Critical Note**: ONLY annotate SA tool source directory that is given by the orchestrator. DO NOT edit anything else other that this directory.

## On Retry: Using Trace Feedback

If the orchestrator provides a **"Feedback from previous annotation attempts"** section, this means the previous annotation was rejected by the trace checker. Act on it:

1. **Read the `[UNSOUNDNESS]` lines found**: If lines were found but the trace check still failed, the caller expression at the injection point produces a different method name than expected. Compare the found lines against the "Required log format" shown in the feedback and move the injection to a point where the correct caller variable is in scope.

2. **No `[UNSOUNDNESS]` lines found**: The injection fired zero times. The injection point is either unreachable for the test case, guarded by a condition that was never true, or in the wrong method entirely. Move to a less-guarded point higher in the call chain (still inside the caller's analysis context).

3. **Required format reminder**: The trace checker parses caller names as `package.classname.methodname`. Your `System.out.println` must emit this exact format. Check that the variable you use for the caller expression produces this format — if the variable holds a descriptor like `Lcom/example/App;.method` you must reformat it.

4. **Do not re-annotate the same location** — if the previous attempt produced no matching lines, choose a different injection point.

5. **Remove the previous annotation**: If you decide to change the location of the annotation, make sure to remove the previous annotation from the tool's source code. This is given by feedback as the file path and injected line. There should be ONLY ONE log statement with [UNSOUNDNESS] in the annotated tool.

5. **Restrict to the unsoundness report**: You should add the logging statement in a way that it gets triggered for the pattern that is identified by the unsoudnness report. You should use `if/else` statements to limit the execution for that particular pattern if necessary. 