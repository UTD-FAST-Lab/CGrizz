package ex.unsafe;

import java.lang.reflect.Field;
import sun.misc.Unsafe;

public class Main {
    interface Target { String targetMethod(); }
    static class UnsafeTarget implements Target { public String targetMethod() { return "unsafe"; } }
    static class SafeTarget implements Target { public String targetMethod() { return "safe"; } }
    static class Holder { Target t = new SafeTarget(); }

    public static void main(String[] args) throws Exception {
        Field f = Unsafe.class.getDeclaredField("theUnsafe");
        f.setAccessible(true);
        Unsafe u = (Unsafe) f.get(null);
        Holder h = new Holder();
        long off = u.objectFieldOffset(Holder.class.getDeclaredField("t"));
        System.out.println(h.t.targetMethod());          // SafeTarget, not a symptom
        u.putObject(h, off, new UnsafeTarget());
        System.out.println(h.t.targetMethod());          // UnsafeTarget, stored via putObject
        Target viaGet = (Target) u.getObject(h, off);
        System.out.println(viaGet.targetMethod());
    }
}
