import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// Dev: Vite (:5173) + proxy de /api pro Flask (:5050), mesma origem, sem
// CORS — mesmo princípio do meta-natal. Produção: o próprio Flask serve o
// build (frontend/dist/), ver app.py — nunca há dois hosts de verdade.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:5050",
        changeOrigin: false,
      },
    },
  },
});
