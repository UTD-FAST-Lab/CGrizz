/** OPAL O22/O23: body-less JDK methods get a synthetic allocation of their declared return type,
 *  except when that type cannot be instantiated (abstract class or interface). */
public class Main {
    public static void main(String[] args) throws Exception {
        ClassLoader cl = Main.class.getClassLoader();                  // returns abstract ClassLoader
        Object r = cl.getResource("Main.class");                       // EDGE
        java.net.URLConnection c = new java.net.URL("file:/tmp").openConnection();  // abstract URLConnection
        int n = c.getContentLength();                                  // EDGE
        String s = String.valueOf(42);                                 // concrete String: modelled
        int k = s.length();                                            // resolved: no warning
        if (r == null && n + k < 0) throw new IllegalStateException();
    }
}
