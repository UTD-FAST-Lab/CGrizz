package com.example;

public class Main {
    // No static field initialization with NEW
    // Class loading happens in main method, not in static initializer

    public static void main(String[] args) {
        // Direct instantiation in main method (not in <clinit>)
        HelperClass helper = new HelperClass();
        System.out.println("Main executed");
        helper.doWork();
    }
}
