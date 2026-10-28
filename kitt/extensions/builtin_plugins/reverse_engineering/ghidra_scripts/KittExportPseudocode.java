// Full bounded pseudocode export for the isolated KITT Ghidra executor.
//@category KITT
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.DecompiledFunction;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

public class KittExportPseudocode extends GhidraScript {
    private static final int MAX_FUNCTIONS = 10000;
    private static final int MAX_FUNCTION_BYTES = 256 * 1024;
    private static final long MAX_TOTAL_BYTES = 512L * 1024L * 1024L;
    private static final int DECOMPILE_SECONDS = 3;

    private static String bounded(String value, int max) {
        if (value == null || value.isBlank()) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }

    private static String quote(String value) {
        if (value == null) value = "";
        StringBuilder builder = new StringBuilder("\"");
        for (int i = 0; i < value.length() && i < 4096; i++) {
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

    private static String sha256(byte[] bytes) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder result = new StringBuilder(64);
        for (byte value : digest) result.append(String.format("%02x", value & 0xff));
        return result.toString();
    }

    private static String safeEntry(String entry) {
        String value = entry.replaceAll("[^A-Za-z0-9_-]", "_");
        return value.length() <= 80 ? value : value.substring(0, 80);
    }

    private static void atomicWrite(Path destination, byte[] bytes) throws IOException {
        Path temporary = Files.createTempFile(destination.getParent(), ".kitt-pseudoc-", ".tmp");
        try {
            Files.write(temporary, bytes);
            try {
                Files.move(temporary, destination, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (AtomicMoveNotSupportedException ignored) {
                Files.move(temporary, destination, StandardCopyOption.REPLACE_EXISTING);
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private static String functionRow(
            String name, String entry, String status, String file, String sha, int bytes, String error
    ) {
        return "{\"name\":" + quote(bounded(name, 160))
                + ",\"entry\":" + quote(bounded(entry, 160))
                + ",\"status\":" + quote(status)
                + ",\"file\":" + quote(file)
                + ",\"sha256\":" + quote(sha)
                + ",\"bytes\":" + bytes
                + ",\"error\":" + quote(bounded(error, 180)) + "}";
    }

    private static String arg(String value, String key) {
        String prefix = key + "=";
        if (value == null || !value.startsWith(prefix)) {
            throw new IllegalArgumentException("Missing " + key + " script argument");
        }
        return value.substring(prefix.length());
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 5 || currentProgram == null) {
            throw new IllegalArgumentException(
                    "Expected output dir, manifest path, source=..., sha256=..., size=...");
        }
        Path outputDir = Path.of(args[0]).toAbsolutePath().normalize();
        Path manifest = Path.of(args[1]).toAbsolutePath().normalize();
        String sourcePath = arg(args[2], "source");
        String sourceSha = arg(args[3], "sha256");
        long sourceSize = Long.parseLong(arg(args[4], "size"));
        Path relativeSource = Path.of(sourcePath);
        if (relativeSource.isAbsolute() || sourcePath.contains("\\")
                || sourcePath.indexOf('\0') >= 0
                || relativeSource.toString().equals(".")
                || relativeSource.normalize().startsWith("..")
                || !sourceSha.matches("[0-9a-f]{64}") || sourceSize <= 0
                || !manifest.equals(outputDir.resolve("manifest.json"))) {
            throw new IllegalArgumentException("Invalid source identity or output paths");
        }
        Files.createDirectories(outputDir);

        DecompInterface decompiler = new DecompInterface();
        decompiler.setOptions(new DecompileOptions());
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(false);
        if (!decompiler.openProgram(currentProgram)) {
            decompiler.dispose();
            throw new IllegalStateException("Ghidra decompiler could not open the program");
        }

        List<String> rows = new ArrayList<>();
        int seen = 0;
        int decompiledCount = 0;
        int failedCount = 0;
        int skippedCount = 0;
        long totalBytes = 0;
        boolean truncated = false;
        String language = currentProgram.getLanguage().toString();
        try {
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                if (seen >= MAX_FUNCTIONS || totalBytes >= MAX_TOTAL_BYTES) {
                    truncated = true;
                    break;
                }
                Function function = functions.next();
                String entry = function.getEntryPoint().toString();
                if (function.isExternal()) {
                    rows.add(functionRow(function.getName(), entry, "SKIPPED_EXTERNAL",
                            "", "", 0, "External function has no body in this binary"));
                    skippedCount++;
                    seen++;
                    continue;
                }
                String status = "FAILED";
                String file = "";
                String digest = "";
                int bytes = 0;
                String error = "";

                try {
                    DecompileResults result = decompiler.decompileFunction(
                            function, DECOMPILE_SECONDS, monitor);
                    DecompiledFunction output = result.getDecompiledFunction();
                    if (!result.decompileCompleted() || output == null) {
                        error = bounded(result.getErrorMessage(), 180);
                        if (error.isEmpty()) error = "Ghidra decompilation did not complete";
                    } else {
                        byte[] code = output.getC().getBytes(StandardCharsets.UTF_8);
                        if (code.length == 0) {
                            error = "Ghidra returned empty pseudocode";
                        } else if (code.length > MAX_FUNCTION_BYTES) {
                            status = "TRUNCATED";
                            error = "Pseudocode exceeds per-function output limit";
                        } else if (totalBytes + code.length > MAX_TOTAL_BYTES) {
                            truncated = true;
                            break;
                        } else {
                            file = String.format("fn_%s_%06d_%s.c",
                                    sourceSha.substring(0, 12), seen, safeEntry(entry));
                            digest = sha256(code);
                            atomicWrite(outputDir.resolve(file), code);
                            bytes = code.length;
                            totalBytes += code.length;
                            status = "DECOMPILED";
                            decompiledCount++;
                        }
                    }
                } catch (Exception exception) {
                    status = "FAILED";
                    file = "";
                    digest = "";
                    bytes = 0;
                    error = bounded(exception.getClass().getSimpleName() + ": "
                            + exception.getMessage(), 180);
                    if (error.isEmpty()) error = "Ghidra decompilation failed";
                }

                if (!"DECOMPILED".equals(status)) {
                    failedCount++;
                    file = "";
                    digest = "";
                    bytes = 0;
                }
                rows.add(functionRow(function.getName(), entry, status, file, digest, bytes, error));
                seen++;
            }
        } finally {
            decompiler.dispose();
        }

        boolean complete = !truncated && decompiledCount > 0 && failedCount == 0;
        String status = complete ? "COMPLETE" : "PARTIAL";
        StringBuilder json = new StringBuilder(Math.max(1024, rows.size() * 400));
        json.append("{\"schema_version\":1,\"status\":").append(quote(status))
                .append(",\"complete\":").append(complete)
                .append(",\"source_path\":").append(quote(sourcePath))
                .append(",\"source_sha256\":").append(quote(sourceSha))
                .append(",\"source_size_bytes\":").append(sourceSize)
                .append(",\"format\":\"ELF\",\"architecture\":").append(quote(language))
                .append(",\"tool\":\"Ghidra Headless\",\"language\":").append(quote(language))
                .append(",\"batch_count\":1,\"batches\":[{\"start_index\":0,\"next_index\":")
                .append(seen).append(",\"truncated\":").append(truncated)
                .append(",\"function_count\":").append(seen)
                .append(",\"decompiled_count\":").append(decompiledCount)
                .append(",\"failed_count\":").append(failedCount)
                .append(",\"skipped_count\":").append(skippedCount).append("}]")
                .append(",\"function_count\":").append(seen)
                .append(",\"decompiled_count\":").append(decompiledCount)
                .append(",\"failed_count\":").append(failedCount)
                .append(",\"skipped_count\":").append(skippedCount)
                .append(",\"truncated\":").append(truncated)
                .append(",\"next_index\":").append(seen)
                .append(",\"functions\":[");
        for (int i = 0; i < rows.size(); i++) {
            if (i > 0) json.append(',');
            json.append(rows.get(i));
        }
        json.append("],\"original_executed\":false}\n");
        byte[] manifestBytes = json.toString().getBytes(StandardCharsets.UTF_8);
        if (manifestBytes.length > 16 * 1024 * 1024) {
            throw new IOException("Pseudocode manifest exceeds the 16 MiB output limit");
        }
        atomicWrite(manifest, manifestBytes);
    }
}
