import sun.misc.Unsafe;

/** S15-style: the result of Unsafe.getObject falls through SPARK's no-op native model. */
public class Main {
    interface Target { String targetMethod(); }
    static class UnsafeTarget implements Target { public String targetMethod() { return "unsafe"; } }
    static class SafeTarget implements Target { public String targetMethod() { return "safe"; } }
    static class Holder { Target t = new SafeTarget(); }

    static Target readVia(Unsafe u, Holder h, long off) {
        return (Target) u.getObject(h, off);
    }

    public static void main(String[] args) throws Exception {
        Unsafe u = Unsafe.getUnsafe();
        Holder h = new Holder();
        long off = u.objectFieldOffset(Holder.class.getDeclaredField("t"));
        Target direct = (Target) u.getObject(h, off);
        System.out.println(direct.targetMethod());               // EDGE: direct
        Target viaHelper = readVia(u, h, off);
        System.out.println(viaHelper.targetMethod());            // EDGE: through a return
        Object o = u.getObject(h, off);
        System.out.println(o.hashCode());                        // EDGE: through Object
        System.out.println(h.t.targetMethod());                  // resolved normally: no warning
    }
}
