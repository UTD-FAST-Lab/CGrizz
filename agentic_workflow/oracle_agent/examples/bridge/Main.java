package ex.bridge;

import java.util.Iterator;

public class Main {
    static class Words implements Iterator<String> {
        int i;
        public boolean hasNext() { return i < 2; }
        public String next() { return "w" + (i++); }    // javac adds bridge next()Ljava/lang/Object;
    }

    public static void main(String[] args) {
        Iterator<String> it = new Words();
        while (it.hasNext()) System.out.println(it.next());
    }
}
