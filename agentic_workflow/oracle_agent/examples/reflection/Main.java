package ex.reflection;

public class Main {
    interface Service { String run(); }
    public static class Impl implements Service { public String run() { return "impl"; } }
    public static class Direct implements Service { public String run() { return "direct"; } }

    public static void main(String[] args) throws Exception {
        String name = System.getProperty("svc", "ex.reflection.Main$" + "Impl");
        Service s = (Service) Class.forName(name).newInstance();
        System.out.println(s.run());
        Service d = new Direct();
        System.out.println(d.run());            // not a symptom: allocated directly
    }
}
