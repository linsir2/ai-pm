import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
// 只自托管两个**拉丁**字体：中文继续走系统字体（Noto Serif SC / Noto Sans SC 已在机器上，
// 全量中文 webfont 几 MB，与 DESIGN.md「系统字体优先、零加载成本」那条理由冲突）。
// 拉丁那两个此前只是写在 font-stack 里、实际静默回退——证据胶囊 `E12` 一直没渲染在
// JetBrains Mono 上。latin 子集各约 21KB，走 node_modules 打包，不引外部 CDN。
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "@fontsource/instrument-serif/latin-400.css";
import "./styles/tokens.css";
import "./styles/app.css";

const mount = document.getElementById("root");
if (mount === null) {
  throw new Error("缺少 #root 挂载点");
}

createRoot(mount).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
