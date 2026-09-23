import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
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
