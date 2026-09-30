/* 闭环区顶上那串"这个项目走过哪几轮"（F4）。

   为什么放这儿：这是**跨轮**的入口。看某一轮只读渲染的通道早就有了
   （点跨轮编号时用的 `historyRound`），缺的只是"有哪些轮可以点"。

   两条规矩：
   1. 默认收起——它不该抢"当前这一轮"的位置；
   2. 每行只说数（主张 / 问卷 / 写入版本），内容点进去看（真的去看那一轮，不在这里复述）。 */

import { useState } from "react";

import type { RoundHeadView } from "../api/types";

export function RoundHistory({
  rounds,
  currentRoundId,
  onView,
}: {
  rounds: RoundHeadView[];
  currentRoundId: string | null;
  onView: (roundId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (rounds.length === 0) return null;

  return (
    <div className="rounds">
      <button
        type="button"
        className="rounds__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        这个项目走过 {rounds.length} 轮 {open ? "▴" : "▾"}
      </button>

      {open && (
        <ol className="rounds__list">
          {rounds.map((item) => {
            const here = item.round_id === currentRoundId;
            return (
              <li className={here ? "rounds__row rounds__row--here" : "rounds__row"} key={item.round_id}>
                <button
                  type="button"
                  className="rounds__open"
                  disabled={here}
                  onClick={() => onView(item.round_id)}
                >
                  <span className="rounds__input">{item.user_input}</span>
                  <span className="muted small">
                    主张 {item.claims_count} · 问卷 {item.questions_count}
                    {item.confirmed_version_no === null
                      ? ""
                      : ` · 写入 v${item.confirmed_version_no}`}
                  </span>
                </button>
                {here && <span className="muted small">（看的就是这一轮）</span>}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
