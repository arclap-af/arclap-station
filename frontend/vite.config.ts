import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Built output lands in dist/ and is served by Caddy from /var/www/arclap.
//
// In dev `npm run dev` proxies /api + /ws to whatever ARCLAP_DEV_BACKEND
// points at. Two common setups:
//
//   1. Local FastAPI on the dev machine (default):
//        npm run dev
//      Edits live-reload in ~100ms, backend is a fake camera + empty DB.
//
//   2. Live Pi (real camera, real schedules, real audit log):
//        npm run dev:pi
//      Equivalent to: ARCLAP_DEV_BACKEND=https://192.168.10.28 npm run dev
//      You'll need to log in once via /login on http://localhost:5173/
//      (the dev server proxies the auth cookie through to the Pi).
//
// To target a different Pi:
//      ARCLAP_DEV_BACKEND=https://arclap-st-90107cb4.local npm run dev
const DEV_BACKEND = process.env.ARCLAP_DEV_BACKEND || "http://127.0.0.1:8000";
const wsBackend = DEV_BACKEND.replace(/^http/, "ws");

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
      "/api": {
        target: DEV_BACKEND,
        changeOrigin: true,
        // The Pi serves its own self-signed cert (Caddy tls internal).
        // We accept it here because the dev server is the only consumer
        // and the connection is on a trusted LAN.
        secure: false,
      },
      "/ws": {
        target: wsBackend,
        ws: true,
        changeOrigin: true,
        secure: false,
      },
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
