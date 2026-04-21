# Non-Reproducing Test Case: No Implicit ClassLoader.loadClass() in Static Initializer

## Overview

This test case serves as a control that does NOT trigger the unsoundness. The `new Helper()` instruction is placed in the `main()` method instead of a static initializer, avoiding the implicit `ClassLoader.loadClass()` call that WALA misses.

## Key Difference from Reproducing Test Case

**Reproducing test case:**
```java
public class App {
    public static Helper instance = new Helper();  // In static initializer
}
```

**Non-reproducing test case (this one):**
```java
public class App {
    public static Helper instance;  // No initialization
    
    public static void main(String[] args) {
        instance = new Helper();  // In main() method
    }
}
```

## Why This Avoids the Unsoundness

When the `new` instruction occurs in `main()` rather than in a static initializer (`<clinit>`), the implicit class loading behavior is different:

1. The JVM has already loaded most classes during program startup
2. The Helper class loading (if it happens) is not directly triggered from within `App.<clinit>()`
3. WALA's call graph doesn't need to model the implicit `ClassLoader.loadClass()` call from `App.<clinit>()` because there is no `new` instruction in `App.<clinit>()`

## Call Path

1. `App.main()` is invoked (entry point)
2. JVM triggers `App.<clinit>()` - but it's empty (no static field initialization logic)
3. `main()` executes `instance = new Helper()`
4. If Helper needs to be loaded, the implicit `ClassLoader.loadClass()` happens from `main()`, not from `<clinit>()`

## Expected Behavior

**WALA Call Graph:**
- No edge from `App.<clinit>()` to `ClassLoader.loadClass()` - and this is CORRECT because `App.<clinit>()` doesn't contain any `new` instructions

## Build and Run

```bash
mvn clean package
java -jar target/implicit-classloading-nonreproducing-1.0-SNAPSHOT.jar
```
