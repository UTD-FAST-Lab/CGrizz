---
name: root-cause-annotator
description: Adds a single targeted log to the SA tool's source code at the exact point where an unsoundness root cause manifests
---

# Root Cause Annotator

Add **exactly one** `[UNSOUNDNESS]` log statement to the SA tool's source, at the point
where the tool gives up on the root cause described in the unsoundness report. The log
tells tool developers and users that *this* pattern made the analysis unsound, so it
must fire for this pattern and nothing else.

Only edit the SA tool source directory you are given.

## Where to put it

Start from `toolLimitation.toolCodeEvidence` (`relevantFiles`, `codeSnippet`) in the report.

- **Caller's analysis context only.** Inject in code that runs while the tool processes
  the *calling* method's call instruction (call-site resolution, reflection handler,
  native/API model, dispatch) — never in code that analyses the callee.
- **Innermost give-up point.** Choose the deepest point, still in the caller's context,
  where the tool definitively drops the edge (returns null/empty, skips the target).
  Typically the failing branch of a guard, e.g. the `else` of
  `if (symbolTable.isStringConstant(use))`.
- **Specific to the pattern.** Add an `if`/`else` or guard so the log fires only when
  this root cause occurs. A log on a path that every call site takes is rejected.

## The log

```java
System.out.println("[UNSOUNDNESS] " + "<description>" + " | caller=" + <caller_expr> + " | site=" + <site_expr>
                   + " | line=" + <line_expr> + " | pc=" + <pc_expr>);
```

- `<description>` — what the tool could not do, in plain words a user of the tool understands
  without reading the report. **At most 60 characters**, no placeholders, no jargon from this
  pipeline. Base it on `toolLimitation.reason`. Examples:
  `"Class.forName name is not a constant"`, `"Proxy class missing from class hierarchy"`,
  `"Unsafe.putObject store not modelled"`, `"implicit ClassCastException not modelled"`.
- `<caller_expr>` — the method **being analysed** (the caller of the missed edge),
  formatted as `package.ClassName.methodName(params)`, e.g.
  `java.nio.charset.CharsetEncoder.replaceWith([B)`. The checker takes the class as
  everything before the last `.` preceding `(`, and the method name after it, so convert
  descriptors like `Lcom/example/App;` to dotted names.
- `<site_expr>` — the call-site reference in scope (e.g. `site`, `callSite`).
- `<line_expr>`, `<pc_expr>` — the source line and the bytecode offset of the **call instruction in
  the caller** whose edge the tool drops. They let each warning be matched to a missed edge. Use
  `-1` for a value the tool does not have, but at least one must be known. Typical sources:
  - WALA: for the invoke instruction `call` in `node`'s IR,
    `int pc = ((IBytecodeMethod<?>) node.getMethod()).getBytecodeIndex(call.iIndex());`
    `int line = node.getMethod().getLineNumber(pc);` (catch `InvalidClassFileException` → `-1`).
  - Soot: for the `Stmt s`, `line = s.getJavaSourceStartLineNumber()`;
    `pc = s.hasTag("BytecodeOffsetTag") ? ((BytecodeOffsetTag) s.getTag("BytecodeOffsetTag")).getBytecodeOffset() : -1`.
  - OPAL: `pc` is in scope where calls are handled; `line = method.body.get.lineNumber(pc).getOrElse(-1)`.
- If the caller/site variables are not in scope, move up the call chain to the nearest
  method (still in the caller's context) that has them.
- Use the tool's language (Java or Scala).

## Unsoundness markers (when the task has a section about them)

For some tools a marker module is installed in the tool copy (see the instructions file named in
the task). When the root cause **loses a value that later reaches calls**, also mark that value at
the give-up point: the tool then prints `[UNSOUNDNESS-EDGE]` at every call the value reaches, so each
missed edge gets its own warning. Guard the mark exactly like the warning.

## How it is checked

The pipeline runs the annotated tool and accepts the annotation only if:

1. on the **reproducing** test case, the log fires with `caller=` naming the root-cause
   method(s) from the oracle, its description is at most 60 characters, and it has `line=` and
   `pc=` (at least one known);
2. it fires for only a small share of all methods the tool analyses (a log in a generic
   place fails this);
3. on the **non-reproducing** test case — the same program without the root cause — it
   does **not** fire for any of that test case's own methods (edge warnings included);
4. the annotated tool computes **the same call graph** on the test case as the original tool:
   the annotation only prints and marks, it never changes the analysis;
5. with a marker module: **every missed edge** of the test case has a warning at its call
   instruction (`[UNSOUNDNESS-EDGE]`, or the root-cause warning when the edge is missed right there).

## On retry

The prompt then contains the previous attempts' feedback, the `[UNSOUNDNESS]` lines that
were printed (for both test cases), and the previous injection location.

- Lines printed, but the check failed → the `caller=` value has the wrong format or names
  the wrong method; move to where the right caller variable is in scope.
- No lines → the location is unreachable for the test case or guarded by a condition that
  never held; move higher in the call chain (still in the caller's context).
- Fired too often, or on the non-reproducing test case → tighten the guard to the
  condition that distinguishes the root cause (compare the two test cases).
- Never re-use a rejected location unchanged. When you move the log, **remove the previous
  one**: the tool must contain exactly one `[UNSOUNDNESS]` statement.
