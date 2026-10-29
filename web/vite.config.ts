import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks to /api; in development Vite forwards that to the FastAPI service.
// In production the company SSO proxy sets the two identity headers (SPEC 6, open item 9).
// The proxy below fakes that for local runs only, so never expose this dev server.
const target = process.env.ASKDB_API ?? "http://127.0.0.1:8000";
const identity = {
  "X-Askdb-User": process.env.ASKDB_DEV_USER ?? "dev",
  "X-Askdb-Role": process.env.ASKDB_DEV_ROLE ?? "admin",
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        headers: identity,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    css: false,
  },
});
