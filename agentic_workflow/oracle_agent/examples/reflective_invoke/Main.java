package ex.reflective_invoke;

import java.lang.reflect.Method;

public class Main {
    public static class Target {
        public static String greet(String who) { return "hi " + who; }
    }

    public static void main(String[] args) throws Exception {
        Class<?> c = Class.forName(System.getProperty("cls", "ex.reflective_invoke.Main$Target"));
        Method m = c.getMethod("greet", String.class);
        Object r = m.invoke(null, "there");
        System.out.println(r);
    }
}
