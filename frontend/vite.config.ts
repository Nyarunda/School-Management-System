import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Dev-only: the browser only ever talks to the Vite origin, so Django needs no CORS
  // config while it stays feature-frozen. Does not affect `vite build`.
  server: { proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } } },
});