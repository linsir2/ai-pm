import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发时把 /api 代理到后端：省掉 CORS，也让前端的 BASE 只有一个写法（`/api`）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // 后端起在别的端口时用 PMSTUDIO_API_TARGET 覆盖，默认还是 8000。
        target: process.env.PMSTUDIO_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
