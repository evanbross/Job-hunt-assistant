import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // In development, proxy /api calls to the Flask backend
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://localhost:5004",
        changeOrigin: true,
      },
    },
  },
  build: {
    // Output to ../frontend/dist so Flask can serve it
    outDir: "dist",
    emptyOutDir: true,
  },
});
