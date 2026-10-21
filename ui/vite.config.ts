import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, `npm run dev` proxies /api to the Python server on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
