import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const gateway = process.env.VITE_GATEWAY_URL ?? "http://127.0.0.1:8000";
const wsTarget = gateway.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/ws": { target: wsTarget, ws: true }, "/health": gateway },
  },
  preview: {
    proxy: { "/ws": { target: wsTarget, ws: true }, "/health": gateway },
  },
  build: { outDir: "dist", sourcemap: true },
});
