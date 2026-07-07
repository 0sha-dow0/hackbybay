import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/repos": "http://127.0.0.1:8000",
      "/incidents": "http://127.0.0.1:8000",
      "/transplants": "http://127.0.0.1:8000"
    }
  },
  preview: {
    port: 4173
  }
});
