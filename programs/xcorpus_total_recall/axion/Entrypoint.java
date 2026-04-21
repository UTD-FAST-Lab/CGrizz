import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.StringTokenizer;
import jazzer.InputParser;

import org.axiondb.tools.Console;

public class Entrypoint {
    public static void entrypoint(String input) throws Exception {
        Path db = Files.createTempDirectory("fuzzing");
        Path logFile = Files.createTempFile("fuzzing", "log");
        PrintWriter logWriter = new PrintWriter(logFile.toFile());
        Console console = null;
        try {
            console = new Console("fuzzingdb", db.toString(), logWriter);
            // createInitialTables(console.getConnection());
            StringTokenizer tokenizer = new StringTokenizer(input, ";", false);
            while (tokenizer.hasMoreTokens()) {
                String sql = tokenizer.nextToken().replace("\n", "");
                try {
                    console.execute(sql);
                } catch (Throwable exc) {
                    // Ignore exceptions
                    exc.printStackTrace(System.err);
                }
            }
        } catch (Throwable exc) {
            // Ignore exceptions
            exc.printStackTrace(System.err);
        } finally {
            console.cleanUp();
            Runtime.getRuntime().exec("rm -rf " + db.toString()).waitFor();
        }
    }

    public static void recurseDirectories(File path) throws Exception {
        for (File inputFile : path.listFiles()) {
            if (inputFile.isFile()) {
                String input = InputParser.parseString(Files.readAllBytes(inputFile.toPath()));
                Entrypoint.entrypoint(input);
            } else {
                recurseDirectories(inputFile);
            }
        }
    }

    public static void main(String args[]) throws Exception {
        recurseDirectories(new File(args[0]));
    }
}
