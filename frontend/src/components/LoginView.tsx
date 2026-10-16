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
    <div className="auth">
      <ProofPanel />

      <main className="auth__entry" aria-label={mode === "login" ? "登录" : "注册"}>
        <div className="auth__card">
          <span className="auth__mark">
            <Logo />
            <span className="auth__name">PM Studio</span>
          </span>

          <h1 className="auth__title">{mode === "login" ? "登录" : "注册"}</h1>
          {/* 副标题**不能提"左边"**：1040px 以下左栏整个撤掉（见 CSS 的 media query），
              任何指向它的文案在窄屏就成了撒谎。这里只说产品在做什么，两种宽度都成立。 */}
          <p className="auth__sub">
            {mode === "login"
              ? "AI 只出草案，你确认之后才写进文档。"
              : "提交后进 pending，等管理员审批通过才能登录。"}
          </p>

          <form className="auth__form" onSubmit={submit}>
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

            <button type="submit" className="btn auth__submit" disabled={pending}>
              {mode === "login" ? "登录" : "注册"}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={switchMode}>
              {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
            </button>
          </form>

          <ApprovalLedger mode={mode} />

          <p className="auth__foot">本机部署 · 数据只在你自己的机器上 · 每个账号只看得到自己的项目</p>
        </div>
      </main>
    </div>
  );
}

/**
 * 左栏：产品真实产出的一块，不是宣传图（`selective truth`——形容词会撒谎，截图不会）。
 *
 * **内容是静态文案，不是活数据**：多用户之后所有端点都要身份，未登录拿不到任何东西，
 * 而为一个展示屏开公开端点不划算（它会把未认证面变大）。代价是这份摘要是**当时的快照**，
 * 不会随项目更新；需要更新时改这里。摘录取自真实项目「真跑验证-20261005-A-订单导出」，
 * 编号（E94/E97/…）是真出处，顺着能回溯。
 */
function ProofPanel() {
  return (
    <aside className="auth__proof">
      <article className="proof">
        <p className="proof__crumb">
          真跑验证-20261005-A-订单导出<span className="proof__ver">v0.4</span>
        </p>
        <h2 className="proof__title">订单导出</h2>

        <h3 className="proof__section">非功能要求</h3>
        <ul className="proof__list">
          <ProofItem refs={["E94", "E102"]}>
            导出文件编码强制为 UTF-8 with BOM，确保主流电子表格软件（如 Excel）兼容。
          </ProofItem>
          <ProofItem refs={["E97", "E104"]}>
            系统必须记录完整的导出操作审计日志，包含：操作人 ID、时间戳、筛选条件摘要、导出字段列表、数据行数及文件哈希。
          </ProofItem>
          <ProofItem refs={["E98"]}>
            后端必须采用流式写入生成 CSV，严禁全量加载到内存，以确保内存占用恒定。
          </ProofItem>
        </ul>

        <h3 className="proof__section">未决</h3>
        <ul className="proof__list">
          <ProofItem refs={["E169"]} open>
            当前生产环境的订单表总数据量级及日均新增数是多少？
            <span className="proof__where">去处：s10</span>
          </ProofItem>
        </ul>

        <p className="proof__note">
          这不是宣传图，是产品真实产出的一块：白底、衬线标题，每句话旁边挂着它的出处。
          点编号能回溯到哪一轮、是谁说的、依据是什么——这是这个产品唯一的信任机制。
        </p>
      </article>
    </aside>
  );
}

function ProofItem({
  refs,
  open = false,
  children,
}: {
  refs: string[];
  open?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className={open ? "proof__item proof__item--open" : "proof__item"}>
      <span className="proof__text">{children}</span>
      <span className="proof__refs">
        {refs.map((ref) => (
          <span className="chip" key={ref}>
            {ref}
          </span>
        ))}
      </span>
    </li>
  );
}

/** 把"注册之后会发生什么"提前讲清楚——别让人注册完才撞墙。用的是证据胶囊那套语言。 */
function ApprovalLedger({ mode }: { mode: "login" | "register" }) {
  const steps = [
    ["提交注册", "用户名 + 密码"],
    ["管理员审批", "后台可看，也可封禁"],
    ["进入工作台", "只看得到自己的项目"],
  ];
  return (
    <div className="ledger">
      <p className="ledger__cap">新账号要走的三步</p>
      <ol className="ledger__steps">
        {steps.map(([title, note], index) => (
          <li
            className={
              mode === "register" && index === 0 ? "ledger__step ledger__step--now" : "ledger__step"
            }
            key={title}
          >
            <span className="ledger__no">{index + 1}</span>
            <span className="ledger__d">
              {title}
              <em>{note}</em>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
