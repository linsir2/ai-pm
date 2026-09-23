import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// 开发时把 /api 代理到后端：省掉 CORS，也让前端的 BASE 只有一个写法（`/api`）。
export default defineConfig(({ mode }) => {
  // 后端起在别的端口时，改 frontend/.env 里的 PMSTUDIO_API_TARGET 即可（shell 变量优先）。
  const env = { ...loadEnv(mode, process.cwd(), ""), ...process.env };
  return {
    plugins: [react()],
    server: {
      port: Number(env.PMSTUDIO_WEB_PORT ?? 5173),
      // 端口被占就报错：这台机器上还跑着别的项目，悄悄换端口会让人打开错的应用。
      strictPort: true,
      proxy: {
        "/api": {
          target: env.PMSTUDIO_API_TARGET ?? "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});
