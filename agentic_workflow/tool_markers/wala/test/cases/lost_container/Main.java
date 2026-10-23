import sun.misc.Unsafe;

/** The lost object is used as a container: SPARK must not see values flow through its fields. */
public class Main {
    interface Target { String targetMethod(); }
    static class StoredTarget implements Target { public String targetMethod() { return "stored"; } }
    static class SafeTarget implements Target { public String targetMethod() { return "safe"; } }
    static class Box { Object v; }
    static class Holder { Box b = new Box(); }

    private String secret() { return "secret"; }

    public static void main(String[] args) throws Exception {
        Unsafe u = Unsafe.getUnsafe();
        Holder h = new Holder();
        long off = u.objectFieldOffset(Holder.class.getDeclaredField("b"));
        Box first = (Box) u.getObject(h, off);
        first.v = new StoredTarget();                            // a store into the lost object
        Box second = (Box) u.getObject(h, off);
        System.out.println(second.hashCode());                   // EDGE: on the lost object
        Object[] arr = (Object[]) u.getObject(h, off);
        arr[0] = new StoredTarget();                             // a store into a lost array
        Object[] arr2 = (Object[]) u.getObject(h, off);
        System.out.println(((Target) arr2[0]).targetMethod());   // its element: the tool knows nothing either way
        System.out.println(((Target) second.v).targetMethod());  // its field: the same
        Box safe = new Box();
        safe.v = new SafeTarget();
        System.out.println(((Target) safe.v).targetMethod());    // resolved normally: no warning
        Main lostMain = (Main) u.getObject(h, off);
        System.out.println(lostMain.secret());                   // a private (special) call on the lost object
    }
}
