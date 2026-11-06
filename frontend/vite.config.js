import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Salida fija en static/dist (sin hashes ni módulos ES): un solo script clásico,
// así funciona aunque Windows sirva los .js con un tipo MIME raro, y el
// actualizador OTA lo transporta como un archivo de texto más.
export default defineConfig({
  plugins: [react()],
  base: "/static/dist/",
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
    target: "chrome110",
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      input: "src/main.jsx",
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "app.js",
        assetFileNames: "app.[ext]",
      },
    },
  },
});
