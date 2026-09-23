/* 底部常驻条：左边是"这一轮的想法"，右边是这一轮现在能做的动作。
   左边的宽度与文档栏对齐，右边的宽度与闭环栏对齐 —— 视线不用跨屏找。 */

import { useState } from "react";

import type { AwaitingMode } from "../api/payload";

export function BottomStrip({
  mode,
  pending,
  drawerOpen,
  onStartRound,
  onOpenDrawer,
  onRetry,
}: {
  mode: AwaitingMode;
  pending: string | null;
  drawerOpen: boolean;
  onStartRound: (input: string) => void;
  onOpenDrawer: () => void;
  onRetry: () => void;
}) {
  const [text, setText] = useState("");
  const accepting = mode === "input" || mode === "done";
  const canSubmit = accepting && pending === null && text.trim() !== "";

  const submit = () => {
    if (!canSubmit) return;
    onStartRound(text.trim());
    setText("");
  };

  return (
    <div className="foot">
      <div className="foot__left">
        <label className="sr-only" htmlFor="round-input">
          这一轮的想法
        </label>
        <textarea
          id="round-input"
          className="input__box"
          value={text}
          readOnly={!accepting}
          disabled={!accepting}
          placeholder={
            accepting
              ? "把这一轮的想法倒进来：一个新功能、一句要求、一个约束，或者你刚想到的点。"
              : "这一轮还没结束，先在上面把它走完。"
          }
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") submit();
          }}
        />
        <div className="input__actions">
          <span className="input__hint">
            {accepting
              ? "Ctrl / ⌘ + Enter 提交。会先复述给你看，你确认了才跑席位。"
              : "一轮走完（写完或失败）才能开下一轮。"}
          </span>
          <button className="btn btn--primary" type="button" disabled={!canSubmit} onClick={submit}>
            {pending === "start" ? "正在复述…" : "开始这一轮"}
          </button>
        </div>
      </div>

      <div className="foot__right">
        <RightActions
          mode={mode}
          pending={pending}
          drawerOpen={drawerOpen}
          onOpenDrawer={onOpenDrawer}
          onRetry={onRetry}
        />
      </div>
    </div>
  );
}

function RightActions({
  mode,
  pending,
  drawerOpen,
  onOpenDrawer,
  onRetry,
}: {
  mode: AwaitingMode;
  pending: string | null;
  drawerOpen: boolean;
  onOpenDrawer: () => void;
  onRetry: () => void;
}) {
  if (mode === "questionnaire" || mode === "draft") {
    if (drawerOpen) {
      return (
        <div className="row">
          <span className="input__hint">
            {mode === "questionnaire" ? "问卷" : "草案卡"}已经展开在右边，随时可以收起来对照文档。
          </span>
        </div>
      );
    }
    return (
      <div className="row">
        <button type="button" className="btn btn--primary" onClick={onOpenDrawer}>
          {mode === "questionnaire" ? "打开问卷" : "打开草案卡"}
        </button>
        <span className="input__hint">抽屉收起来了，这一轮还停在这儿等你决定。</span>
      </div>
    );
  }

  if (mode === "opening") {
    return (
      <div className="row">
        <span className="input__hint">复述卡在上面：看完改完，点「确认，开跑席位」。</span>
      </div>
    );
  }

  if (mode === "failed") {
    return (
      <div className="row">
        <button
          type="button"
          className="btn btn--primary"
          onClick={onRetry}
          disabled={pending !== null}
        >
          {pending === "retry" ? "正在接着跑…" : "接着跑这一轮"}
        </button>
        <span className="input__hint">只补没交回的席位，编号不会重发。</span>
      </div>
    );
  }

  if (mode === "loading") {
    return (
      <div className="row">
        <span className="input__hint">
          {pending === null ? "这一轮停在执行中。" : "席位在跑，会一个个交回（几十秒）。"}
        </span>
        {pending === null && (
          <button type="button" className="btn btn--sm" onClick={onRetry}>
            接着跑
          </button>
        )}
      </div>
    );
  }

  return <span className="input__hint">随时可以把新想法倒进来，一轮一轮长文档。</span>;
}
