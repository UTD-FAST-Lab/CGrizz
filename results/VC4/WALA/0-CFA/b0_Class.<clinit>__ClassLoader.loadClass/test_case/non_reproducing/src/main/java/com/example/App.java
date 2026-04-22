package com.example;

/**
 * Non-reproducing test case: No implicit ClassLoader.loadClass() call.
 *
 * This test demonstrates a semantically similar program that does NOT trigger
 * the implicit ClassLoader.loadClass call during static initialization.
 *
 * The static field is initialized to null instead of 'new Helper()', so the
 * JVM does not need to load the Helper class during <clinit>() execution.
 * The Helper instance is created later in main(), where the class loading
 * happens in a regular method context (not during static initialization).
 *
 * WALA can analyze this call graph correctly because there is no implicit
 * class loading in the static initializer.
 */
public class App {

    /**
     * Static field initialized to null - no class loading required.
     * This avoids the implicit ClassLoader.loadClass call in <clinit>().
     */
    static Object helper = null;

    public static void main(String[] args) {
        // Helper class is loaded here during normal execution,
        // not during static initialization.
        // WALA can properly track this call path.
        helper = new Helper();
        System.out.println("App loaded, helper created: " + helper);
    }
}
