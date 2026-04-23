# Non-Reproducing Test Case: Direct Instantiation in Main Method

## Description

This test case does NOT reproduce the unsoundness. The `NEW` instruction for `HelperClass` occurs in the `main()` method, not in the static initializer (`<clinit>`), so the implicit `ClassLoader.loadClass()` call is associated with `Main.main()` rather than `Main.<clinit>()`.

## Call Path

1. JVM loads `Main` class
2. `Main.<clinit>()` executes (but does nothing - no static fields to initialize)
3. `Main.main()` is invoked
4. Inside `main()`, `new HelperClass()` is executed
5. JVM implicitly calls `ClassLoader.loadClass("com.example.HelperClass")` from within `main()` context
6. `HelperClass.<clinit>()` executes
7. `HelperClass` instance is created

## Expected Behavior

**Runtime (JVM):** The edge `Main.main() -> ClassLoader.loadClass(String)` exists (not `Main.<clinit>() -> ClassLoader.loadClass(String)`).

**WALA 0-CFA:** WALA will still abstract away the `ClassLoader.loadClass()` call, but the missed edge would be from `Main.main()` to `ClassLoader.loadClass()`, not from `Main.<clinit>()` as in the original boundary.

## Root Cause Removed

By moving the `NEW` instruction from the static initializer to the main method, we remove the specific pattern where `<clinit>() -> ClassLoader.loadClass()` is triggered. The class loading now happens in a different context (main method execution rather than class initialization).

## Build and Run

```bash
mvn clean package
java -jar target/class-loading-non-reproducing-1.0-SNAPSHOT.jar
```

## Difference from Reproducing Test Case

The reproducing test case has a static field initialized with `new HelperClass()`, which triggers `ClassLoader.loadClass()` from within `Main.<clinit>()`. This test case instantiates `HelperClass` inside `main()`, so the class loading (if modeled) would be associated with `Main.main()` instead. The specific unsoundness boundary `Main.<clinit>() -> ClassLoader.loadClass()` is not triggered.
