package com.example;

/**
 * Control test case WITHOUT the root cause.
 *
 * The 'new Helper()' instruction is in the main() method, not in a static initializer.
 * By the time main() executes, all classes referenced in the static context have already
 * been loaded, so the implicit ClassLoader.loadClass() call (if any) happens earlier
 * in the program initialization, not directly from this code path.
 *
 * WALA can analyze this more accurately because there's no 'new' instruction in <clinit>.
 */
public class App {
    // No static field initialization with 'new' instruction
    public static Helper instance;

    public static void main(String[] args) {
        // Instantiation happens in main(), not in static initializer
        instance = new Helper();
        System.out.println("Helper instance: " + instance);
    }
}
