/// <reference types="vitest/config" />
import { defineConfig } from "vite";

// Proxy de mesma origem para o resource server Mainnet público.
const API_TARGET = process.env.AHX_API_URL ?? "https://alpha-hunter-x402-production.up.railway.app";

const proxy = {
  "/api": {
    target: API_TARGET,
    changeOrigin: true,
    rewrite: (p: string) => p.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  define: { global: "globalThis" },
  server: { host: "127.0.0.1", port: 5174, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 4174, strictPort: true, proxy },
  test: {
    environment: "node",
    setupFiles: ["tests/setup.ts"],
  },
});
