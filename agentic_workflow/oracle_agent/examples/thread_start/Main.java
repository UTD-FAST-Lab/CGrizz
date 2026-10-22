package ex.thread_start;

public class Main {
    static class Worker extends Thread {
        public void run() { System.out.println("run"); }
    }

    public static void main(String[] args) throws Exception {
        Worker w = new Worker();
        w.start();
        w.join();
    }
}
