import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const rootDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
    plugins: [react(), tailwindcss()],
    build: {
        rollupOptions: {
            input: {
                site: `${rootDir}/index.html`,
                docs: `${rootDir}/docs.html`,
            },
        },
    },
    server: {
        port: 5173,
        // Encaminha /api e /auth pro backend FastAPI em dev (evita problema de CORS).
        proxy: {
            "/auth": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/guilds": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/meta": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/players": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/render": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/claims": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/profile": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/craft": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/market-history": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/companion": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/scan": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
        },
    },
});
