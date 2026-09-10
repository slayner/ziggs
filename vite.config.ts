import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Without this, Vite watches the entire src-tauri/target directory (17 GB+
      // of Cargo build output). When the linker locks a .dll for writing, the Node
      // watcher crashes on an unhandled EBUSY and brings down the whole `npm run dev`.
      ignored: ["**/src-tauri/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "esnext",
    outDir: "dist",
  },
});