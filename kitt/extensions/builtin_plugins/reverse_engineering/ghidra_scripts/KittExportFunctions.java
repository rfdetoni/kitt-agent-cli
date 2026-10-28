// Operator-controlled, read-only Ghidra pseudocode export.
//@category KITT
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.DecompiledFunction;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

public class KittExportFunctions extends GhidraScript {
    private static final int MAX_BATCH_FUNCTIONS = 128;
    private static final int MAX_FUNCTION_PSEUDOCODE_BYTES = 256 * 1024;
    private static final int DECOMPILE_SECONDS = 3;

    private static String quote(String value) {
        if (value == null) return "\"\"";
        StringBuilder builder = new StringBuilder("\"");
        for (int i = 0; i < value.length() && i < 500; i++) {
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

    private static String safeFileName(String entry) {
        return "fn_" + entry.replaceAll("[^A-Za-z0-9_-]", "_") + ".c";
    }

    private static String boundedError(String value) {
        if (value == null || value.isBlank()) return "Ghidra did not return decompiled C";
        return value.length() <= 500 ? value : value.substring(0, 500);
    }

    private static void writeFunction(
            BufferedWriter writer, Function function, DecompInterface decompiler, Path outputDir
    ) throws IOException {
        String entry = function.getEntryPoint().toString();
        String filename = "";
        String status = "FAILED";
        String error = "";
        int pseudocodeBytes = 0;
        try {
            DecompileResults result = decompiler.decompileFunction(function, DECOMPILE_SECONDS, monitor);
            DecompiledFunction decompiled = result.getDecompiledFunction();
            if (!result.decompileCompleted() || decompiled == null) {
                error = boundedError(result.getErrorMessage());
            } else {
                String code = decompiled.getC();
                byte[] raw = code == null ? new byte[0] : code.getBytes(StandardCharsets.UTF_8);
                if (raw.length == 0) {
                    error = "Ghidra returned empty pseudocode";
                } else if (raw.length > MAX_FUNCTION_PSEUDOCODE_BYTES) {
                    status = "TRUNCATED";
                    error = "Pseudocode exceeds the per-function output limit";
                } else {
                    filename = safeFileName(entry);
                    Files.write(outputDir.resolve(filename), raw,
                            StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
                    status = "DECOMPILED";
                    pseudocodeBytes = raw.length;
                }
            }
        } catch (Exception exception) {
            status = "FAILED";
            filename = "";
            pseudocodeBytes = 0;
            error = boundedError(exception.getClass().getSimpleName() + ": " + exception.getMessage());
        }
        if (!"DECOMPILED".equals(status)) filename = "";
        writer.write("{\"name\":" + quote(function.getName())
                + ",\"entry\":" + quote(entry)
                + ",\"status\":" + quote(status)
                + ",\"file\":" + quote(filename)
                + ",\"error\":" + quote(error)
                + ",\"pseudocode_bytes\":" + pseudocodeBytes + "}");
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 4 || currentProgram == null) {
            throw new IllegalArgumentException(
                    "Expected pseudocode output directory, report path, start index, max functions");
        }
        int startIndex = Integer.parseInt(args[2]);
        int maxFunctions = Integer.parseInt(args[3]);
        if (startIndex < 0 || maxFunctions < 1 || maxFunctions > MAX_BATCH_FUNCTIONS) {
            throw new IllegalArgumentException("Invalid bounded batch arguments");
        }
        Path outputDir = Path.of(args[0]).toAbsolutePath();
        Path report = Path.of(args[1]).toAbsolutePath();
        Files.createDirectories(outputDir);
        Files.createDirectories(report.getParent());

        DecompInterface decompiler = new DecompInterface();
        decompiler.setOptions(new DecompileOptions());
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(false);
        if (!decompiler.openProgram(currentProgram)) {
            decompiler.dispose();
            throw new IllegalStateException("Ghidra decompiler could not open the program");
        }

        int functionIndex = 0;
        int selected = 0;
        boolean truncated = false;
        int nextIndex;
        try (BufferedWriter writer = Files.newBufferedWriter(report, StandardCharsets.UTF_8)) {
            writer.write("{\"language\":" + quote(currentProgram.getLanguage().toString())
                    + ",\"start_index\":" + startIndex + ",\"functions\":[");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                if (functionIndex < startIndex) {
                    functionIndex++;
                    continue;
                }
                if (selected >= maxFunctions) {
                    truncated = true;
                    break;
                }
                if (selected++ > 0) writer.write(",");
                functionIndex++;
                writeFunction(writer, function, decompiler, outputDir);
            }
            if (startIndex > functionIndex) {
                throw new IllegalArgumentException("Start index exceeds function count");
            }
            nextIndex = functionIndex;
            writer.write("],\"truncated\":" + truncated + ",\"next_index\":" + nextIndex + "}");
        } finally {
            decompiler.dispose();
        }
    }
}
