package com.example;

/**
 * Helper class that is loaded during normal execution in main(),
 * not during static initialization.
 *
 * In this non-reproducing test, the JVM loads this class when
 * main() executes 'new Helper()', not during <clinit>().
 */
public class Helper {

    public Helper() {
        // Simple constructor
    }

    @Override
    public String toString() {
        return "Helper instance";
    }
}
