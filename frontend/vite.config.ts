import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Built output lands in dist/ and is served by Caddy from /var/www/arclap.
// In dev we proxy /api and /ws to the local FastAPI backend (port 8000).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      // Tests live one directory up — point all bare imports at frontend/node_modules.
      "@testing-library/react": path.resolve(__dirname, "node_modules/@testing-library/react"),
      "@testing-library/jest-dom": path.resolve(__dirname, "node_modules/@testing-library/jest-dom"),
      "@testing-library/user-event": path.resolve(__dirname, "node_modules/@testing-library/user-event"),
      "@tanstack/react-query": path.resolve(__dirname, "node_modules/@tanstack/react-query"),
      vitest: path.resolve(__dirname, "node_modules/vitest"),
      react: path.resolve(__dirname, "node_modules/react"),
      "react-dom": path.resolve(__dirname, "node_modules/react-dom"),
      "react-router-dom": path.resolve(__dirname, "node_modules/react-router-dom"),
      zod: path.resolve(__dirname, "node_modules/zod"),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["../tests/frontend/**/*.test.{ts,tsx}"],
    server: {
      deps: { inline: [] },
    },
  },
});
