package ex.static_field;

import java.io.PrintStream;

public class Main {
    public static void main(String[] args) {
        System.out.println("hello");
        PrintStream mine = new PrintStream(System.err);
        mine.flush();                                    // not a symptom
    }
}
