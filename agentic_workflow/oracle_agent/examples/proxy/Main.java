package ex.proxy;

import java.lang.reflect.Proxy;

public class Main {
    public interface Foo { String bar(); }

    public static void main(String[] args) {
        Foo f = (Foo) Proxy.newProxyInstance(Main.class.getClassLoader(), new Class<?>[]{Foo.class},
                (proxy, m, a) -> "proxied " + m.getName());
        System.out.println(f.bar());
    }
}
