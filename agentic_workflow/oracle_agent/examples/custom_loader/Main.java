package ex.custom_loader;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/** A class defined at run time from bytes is not in the tool's class hierarchy (W2, S3, O3). */
public class Main {
    public interface Cmp { int compare(int a, int b); String name(); }
    public static class IntCmp implements Cmp {
        public int compare(int a, int b) { return Integer.compare(a, b); }
        public String name() { return "int"; }
    }

    static class BytesLoader extends ClassLoader {
        BytesLoader(ClassLoader parent) { super(parent); }

        Class<?> define(String name) throws Exception {
            InputStream in = getParent().getResourceAsStream(name.replace('.', '/') + ".class");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            byte[] b = out.toByteArray();
            return defineClass(name, b, 0, b.length);
        }
    }

    public static void main(String[] args) throws Exception {
        Cmp normal = (Cmp) IntCmp.class.newInstance();                       // same API, normal loader
        System.out.println(normal.name());
        Class<?> k = new BytesLoader(Main.class.getClassLoader()).define("ex.custom_loader.Main$IntCmp");
        Cmp viaBytes = (Cmp) k.newInstance();
        System.out.println(viaBytes.compare(1, 2));
    }
}
