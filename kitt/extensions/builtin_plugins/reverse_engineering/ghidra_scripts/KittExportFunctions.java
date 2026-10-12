// Operator-controlled, read-only Ghidra headless analysis export.
//@category KITT
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public class KittExportFunctions extends GhidraScript {
    private static String quote(String value) {
        if (value == null) return "\"\"";
        StringBuilder builder = new StringBuilder("\"");
        for (int i = 0; i < value.length() && i < 160; i++) {
            char c = value.charAt(i);
            if (c == '\\' || c == '"') builder.append('\\').append(c);
            else if (c == '\n') builder.append("\\n");
            else if (c == '\r') builder.append("\\r");
            else if (c == '\t') builder.append("\\t");
            else if (c < 0x20) builder.append(String.format("\\u%04x", (int)c));
            else builder.append(c);
        }
        return builder.append('"').toString();
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1 || currentProgram == null) {
            throw new IllegalArgumentException("Expected output path and current program");
        }
        int count = 0;
        boolean truncated = false;
        try (BufferedWriter writer = Files.newBufferedWriter(
                Path.of(args[0]), StandardCharsets.UTF_8)) {
            writer.write("{\"language\":" + quote(currentProgram.getLanguage().toString()) + ",\"functions\":[");
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled();
                if (count == 512) {
                    truncated = true;
                    break;
                }
                if (count++ > 0) writer.write(",");
                writer.write("{\"name\":" + quote(function.getName()) +
                        ",\"entry\":" + quote(function.getEntryPoint().toString()) + "}");
            }
            writer.write("],\"truncated\":" + truncated + "}");
        }
    }
}
