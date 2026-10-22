package ex.jdk_internal_class;

import javax.xml.parsers.DocumentBuilderFactory;

/** Soot excludes com.sun.* by default, so the JAXP implementation is not in its Scene (S36). */
public class Main {
    public static class MyFactory {
        public static MyFactory newInstance() { return new MyFactory(); }
        public String make() { return "mine"; }
    }

    public static void main(String[] args) throws Exception {
        DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();     // a com.sun.* implementation
        System.out.println(dbf.newDocumentBuilder() != null);
        MyFactory f = MyFactory.newInstance();                                 // application class: resolved
        System.out.println(f.make());
    }
}
