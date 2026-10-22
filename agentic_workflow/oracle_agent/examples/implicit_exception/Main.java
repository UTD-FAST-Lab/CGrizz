package ex.implicit_exception;

public class Main {
    static Object value() { return Integer.valueOf(1); }

    static void worker(String s) {
        System.out.println(s.length());                  // NPE thrown by the JVM
    }

    public static void main(String[] args) throws Exception {
        try {
            String s = (String) value();                 // CCE thrown by the JVM
            System.out.println(s);
        } catch (ClassCastException e) {
            System.out.println("caught CCE");
        }
        try {
            throw new IllegalStateException("explicit"); // explicit: not linked to CHECKCAST
        } catch (IllegalStateException e) {
            System.out.println("caught ISE");
        }
        Thread t = new Thread(() -> worker(null));
        t.start();
        t.join();
    }
}
