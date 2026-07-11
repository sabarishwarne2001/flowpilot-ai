import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },

  server: {
    host: true,
    port: 3000,
    strictPort: true,
    open: true,
  },

  preview: {
    host: true,
    port: 3000,
    strictPort: true,
  },

  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
  },
});