package ex.nonpublic_ctor;

/** WALA's Class.newInstance model allocates only classes with a public no-arg constructor (W37). */
public class Main {
    public interface Db { String query(); }
    public static class PublicDb implements Db { public PublicDb() {} public String query() { return "pub"; } }
    public static class HiddenDb implements Db { HiddenDb() {} public String query() { return "hidden"; } }

    public static void main(String[] args) throws Exception {
        Db a = (Db) Class.forName(System.getProperty("a", "ex.nonpublic_ctor.Main$PublicDb")).newInstance();
        Db b = (Db) Class.forName(System.getProperty("b", "ex.nonpublic_ctor.Main$HiddenDb")).newInstance();
        System.out.println(a.query() + b.query());
    }
}
