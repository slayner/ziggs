import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Sem isso o Vite observa src-tauri/target inteiro (17GB+ de build do
      // Cargo) — quando o linker trava um .dll pra escrever, o watcher do
      // Node explode com EBUSY não tratado e derruba o `npm run dev` inteiro.
      ignored: ["**/src-tauri/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "esnext",
    outDir: "dist",
  },
});