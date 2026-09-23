import { useState } from "react";

import { Banner } from "./States";

/** 一个项目都没有时的第一屏：建项目 = 造一份 13 章空骨架。 */
export function Onboarding({
  onCreate,
  error,
  pending,
}: {
  onCreate: (title: string) => void;
  error: string | null;
  pending: boolean;
}) {
  const [title, setTitle] = useState("");

  return (
    <div className="empty">
      <p className="muted small mono">PM Studio</p>
      <h1 className="empty__title">先建一个项目</h1>
      <p className="empty__text">
        一个项目 = 一份 13 章的产品文档骨架。骨架结构固定，内容靠一轮一轮长出来：
        你把想法倒进来，系统复述、请领域席位发言、有疑问就出问卷，最后由你在草案卡上勾要哪些，
        勾完才真正写进文档，每写一次多一版。
      </p>
      {error !== null && <Banner>{error}</Banner>}
      <form
        className="field-inline"
        onSubmit={(event) => {
          event.preventDefault();
          const clean = title.trim();
          if (clean !== "") onCreate(clean);
        }}
      >
        <label className="sr-only" htmlFor="project-title">
          项目名（一般就是产品名）
        </label>
        <input
          id="project-title"
          className="text-input"
          value={title}
          placeholder="项目名，例如：订单中台"
          onChange={(event) => setTitle(event.target.value)}
          autoFocus
        />
        <button className="btn btn--primary" type="submit" disabled={pending || title.trim() === ""}>
          {pending ? "正在建…" : "建项目"}
        </button>
      </form>
    </div>
  );
}
