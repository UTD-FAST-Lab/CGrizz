package ex.abstract_return;

/** OPAL's synthetic return values of body-less JDK methods skip non-instantiable types (O22, O23). */
public class Main {
    public static void main(String[] args) {
        ClassLoader cl = Main.class.getClassLoader();               // declared type ClassLoader is abstract
        System.out.println(cl.getResource("ex/abstract_return/Main.class") != null);
        String s = String.valueOf(42);                              // declared type String is concrete
        System.out.println(s.length());
    }
}
