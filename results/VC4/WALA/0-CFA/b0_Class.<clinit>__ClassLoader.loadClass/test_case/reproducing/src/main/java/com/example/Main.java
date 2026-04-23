package com.example;

public class Main {
    // Static field initialized with NEW instruction
    // This triggers implicit ClassLoader.loadClass(HelperClass) during class initialization
    static HelperClass helper = new HelperClass();

    public static void main(String[] args) {
        System.out.println("Main executed");
        helper.doWork();
    }
}
