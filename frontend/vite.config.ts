import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Encaminha /api e /auth pro backend FastAPI em dev (evita problema de CORS).
    proxy: {
      "/auth": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/guilds": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/meta": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/players": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/render": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
