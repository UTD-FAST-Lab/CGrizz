package ex.conditional_forname;

/** Tools resolve Class.forName only for string constants (e.g. WALA's isStringConstant). */
public class Main {
    interface Svc { String run(); }
    public static class Fixed implements Svc { public String run() { return "fixed"; } }
    public static class Local implements Svc { public String run() { return "local"; } }
    public static class Dyn implements Svc { public String run() { return "dyn"; } }
    public static class Phi implements Svc { public String run() { return "phi"; } }

    public static void main(String[] args) throws Exception {
        Svc fixed = (Svc) Class.forName("ex.conditional_forname.Main$Fixed").newInstance();   // constant: resolved
        String n = "ex.conditional_forname.Main$Local";
        Svc local = (Svc) Class.forName(n).newInstance();                                     // constant via a local
        Svc dyn = (Svc) Class.forName(System.getProperty("svc", "ex.conditional_forname.Main$Dyn")).newInstance();
        String m = args.length > 5 ? args[0] : "ex.conditional_forname.Main$Phi";             // constant or parameter
        Svc phi = (Svc) Class.forName(m).newInstance();
        System.out.println(fixed.run() + local.run() + dyn.run() + phi.run());
    }
}
