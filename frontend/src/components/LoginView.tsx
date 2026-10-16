/* 登录 / 注册一屏两态：同一个表单换标题与提交动作，不另开一页。
   提交成功与否由 AuthProvider 决定——成功（登录）后 App 守卫自然切走；
   注册成功只提示"等审批"，不自动登录（pending 登不进）。 */

import { useState } from "react";

import { ApiError } from "../api/client";
import { useAuth } from "../state/auth";
import { Logo } from "./Logo";

export function LoginView() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNotice(null);
    void (async () => {
      try {
        if (mode === "login") {
          await login(username, password);
        } else {
          await register(username, password);
          setMode("login");
          setPassword("");
          setNotice("注册成功，等管理员审批通过后即可登录。");
        }
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "请求失败");
      } finally {
        setPending(false);
      }
    })();
  };

  const switchMode = () => {
    setMode(mode === "login" ? "register" : "login");
    setError(null);
    setNotice(null);
  };

  return (
    <div className="shell shell--plain">
      <main className="doc" aria-label={mode === "login" ? "登录" : "注册"}>
        <div className="auth">
        <form className="auth__card stack" onSubmit={submit}>
          <span className="wordmark">
            <Logo />
            <span className="wordmark__name">PM Studio</span>
          </span>
          <h1 className="muted small">{mode === "login" ? "登录" : "注册"}</h1>

          <label className="auth__label" htmlFor="auth-username">用户名</label>
          <input
            id="auth-username"
            className="input"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />

          <label className="auth__label" htmlFor="auth-password">密码</label>
          <input
            id="auth-password"
            className="input"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          {error !== null && <p role="alert" className="banner banner--error">{error}</p>}
          {notice !== null && <p role="status" className="banner banner--ok">{notice}</p>}

          <button type="submit" className="btn" disabled={pending}>
            {mode === "login" ? "登录" : "注册"}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={switchMode}>
            {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
          </button>
        </form>
        </div>
      </main>
    </div>
  );
}
