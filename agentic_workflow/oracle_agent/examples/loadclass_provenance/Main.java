package ex.loadclass_provenance;

/** OPAL resolves Class.newInstance for class constants, forName and getClass, not for loadClass (O3). */
public class Main {
    public interface Task { String go(); }
    public static class A implements Task { public String go() { return "a"; } }
    public static class B implements Task { public String go() { return "b"; } }
    public static class C implements Task { public String go() { return "c"; } }

    static Task make(Class<?> c) throws Exception {
        return (Task) c.newInstance();                           // receiver is a parameter: decided at run time
    }

    public static void main(String[] args) throws Exception {
        ClassLoader l = Main.class.getClassLoader();
        Class<?> a = l.loadClass("ex.loadclass_provenance.Main$A");
        Task ta = (Task) a.newInstance();                        // receiver from loadClass in this method
        Task tb = (Task) B.class.newInstance();                  // class literal: resolved
        Task tc = make(l.loadClass("ex.loadclass_provenance.Main$C"));
        System.out.println(ta.go() + tb.go() + tc.go());
    }
}
