import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, Vite proxies /api to the Python server: :8000 by default, the dev API (:8010) under scripts/dev.sh.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": process.env.DARWIN_API ?? "http://127.0.0.1:8000" } },
  build: { chunkSizeWarningLimit: 700 }, // three.js, loaded only by the 3D preview
});
