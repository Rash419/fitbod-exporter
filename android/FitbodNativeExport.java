import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.json.*;

/** Read-only history export using Android's HTTPS implementation.
 * Run as the authorized ADB shell user; no app installation or root required.
 * Session comes from stdin, stays in memory, and is never logged.
 */
public final class FitbodNativeExport {
    private static File output;
    private static final String BASE = "https://nautilus.fitbod.me";

    private static void save(String name, String value) throws IOException {
        try (FileOutputStream stream = new FileOutputStream(new File(output, name))) {
            stream.write(value.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static String read(InputStream stream) throws IOException {
        if (stream == null) return "";
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] chunk = new byte[16384];
            int n;
            while ((n = in.read(chunk)) != -1) {
                if (out.size() + n > 64 * 1024 * 1024) throw new IOException("Response exceeds safety limit");
                out.write(chunk, 0, n);
            }
            return out.toString("UTF-8");
        }
    }

    private static JSONObject call(String path, JSONObject body, String access) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(BASE + path).openConnection();
        conn.setConnectTimeout(25000);
        conn.setReadTimeout(25000);
        conn.setInstanceFollowRedirects(false);
        conn.setRequestProperty("User-Agent", "fitbod-android/8.28.0-2");
        conn.setRequestProperty("Accept", access == null ? "application/json" : "application/vnd.api+json");
        conn.setRequestProperty("Content-Type", "application/json");
        if (access != null) conn.setRequestProperty("Authorization", "Bearer " + access);
        try {
            if (body != null) {
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                byte[] encoded = body.toString().getBytes(StandardCharsets.UTF_8);
                conn.setFixedLengthStreamingMode(encoded.length);
                try (OutputStream stream = conn.getOutputStream()) { stream.write(encoded); }
            }
            int status = conn.getResponseCode();
            String raw = read(status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream());
            if (status < 200 || status >= 300) {
                JSONObject error = new JSONObject();
                error.put("path", path);
                error.put("status", status);
                error.put("server", conn.getHeaderField("Server"));
                error.put("content_type", conn.getHeaderField("Content-Type"));
                error.put("cf_ray", conn.getHeaderField("CF-Ray"));
                error.put("body", raw);
                save("http-error.json", error.toString(2));
                throw new IOException("HTTP " + status + " at " + path.split("\\?")[0]);
            }
            return new JSONObject(raw);
        } finally { conn.disconnect(); }
    }

    public static void main(String[] args) {
        try {
            if (args.length < 1 || args.length > 2 || !args[0].startsWith("/data/local/tmp/fitbod-native-"))
                throw new IOException("Expected a dedicated temporary export directory");
            output = new File(args[0], "results");
            if (!output.mkdirs()) throw new IOException("Export directory already exists or is unavailable");
            save("status.json", new JSONObject().put("complete", false).toString());
            String refresh = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8)).readLine();
            if (refresh == null || refresh.trim().isEmpty()) throw new IOException("No session provided on stdin");
            JSONObject auth = call("/access_token", new JSONObject().put("refresh_token", refresh.trim()), null);
            refresh = null;
            String access = auth.getString("access_token");
            System.out.println("Android token exchange succeeded.");
            if (args.length == 2 && args[1].equals("catalogue")) {
                JSONObject custom = call("/api/v3/custom_exercises", null, access);
                save("custom-exercises.json", custom.toString());
                save("status.json", new JSONObject().put("complete", true)
                        .put("custom_exercises", custom.getJSONArray("data").length()).toString(2));
                System.out.println("Custom exercise catalogue fetched: " + custom.getJSONArray("data").length() + " entries.");
                return;
            }
            Set<String> seen = new HashSet<>();
            for (int page = 1; page <= 1000; page++) {
                if (page > 1) Thread.sleep(1000);
                String path = "/api/v3/workout_data?page[size]=1000&sort=-date_performed";
                if (page > 1) path += "&page[number]=" + page;
                JSONObject doc = call(path, null, access);
                JSONArray rows = doc.getJSONArray("data");
                save(String.format(Locale.ROOT, "page-%05d.json", page), doc.toString());
                System.out.println("Android history page " + page + ": " + rows.length() + " workouts");
                if (rows.length() == 0) {
                    JSONObject status = new JSONObject().put("complete", true).put("workouts", seen.size());
                    save("status.json", status.toString(2));
                    System.out.println("Android bulk fetch complete: " + seen.size() + " unique workouts.");
                    return;
                }
                int before = seen.size();
                for (int i = 0; i < rows.length(); i++) seen.add(rows.getJSONObject(i).getString("id"));
                if (seen.size() == before) throw new IOException("Pagination repeated; export is incomplete");
            }
            throw new IOException("Page safety limit reached; export is incomplete");
        } catch (Exception e) {
            // No stack traces, response bodies, or token values on stdout/stderr.
            System.err.println("Android export stopped: " + e.getClass().getSimpleName()
                    + (e instanceof IOException ? ": " + e.getMessage() : ""));
            System.exit(1);
        }
    }
}
