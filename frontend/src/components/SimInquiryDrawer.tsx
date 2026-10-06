/* 模拟抽屉（M10）：待验证假设逐条勾选收下，默认全空。

   切片是分组（不可勾）；假设挂切片下分组展示，挂不上的进"其他"（后端级联丢条后
   正常到不了这里，但抽屉不替后端保证——看到了就说明后端有 bug）。
   卡片里**不能改内容**（与草案卡同一条纪律），只有勾 / 不勾。 */

import { useState } from "react";

import type { SimInquiryView } from "../api/types";

export function SimInquiryDrawer({
  inquiry,
  pending,
  initialChosen,
  onKeep,
  onClose,
}: {
  /** null = 还没跑出来（pending 时显示"正在推演"，失败时显示"关掉重来"）。 */
  inquiry: SimInquiryView | null;
  pending: boolean;
  /** 上次勾过的（刷新回来不丢选择；新跑一份会由调用方清掉）。 */
  initialChosen: string[];
  onKeep: (ids: string[]) => void;
  onClose: () => void;
}) {
  const [checked, setChecked] = useState<string[]>(initialChosen);
  const toggle = (id: string) =>
    setChecked((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );

  const knownSlices = new Set((inquiry?.slices ?? []).map((item) => item.local_id));
  const orphans = (inquiry?.hypotheses ?? []).filter(
    (item) => !knownSlices.has(item.slice_id),
  );

  if (inquiry === null) {
    return (
      <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="sim-title">
        <header className="drawer__head">
          <h2 className="drawer__title" id="sim-title">
            模拟调研 · 待验证的假设（不是用户数据）
          </h2>
          <span className="topbar__spacer" />
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
            收起
          </button>
        </header>
        <div className="drawer__body">
          <p className="input__hint">
            {pending
              ? "正在推演（一次结构化调用，约几十秒），跑出来自动出现在这里。"
              : "上一次没跑出来（看顶部报错），关掉重来。"}
          </p>
        </div>
      </aside>
    );
  }

  const row = (
    id: string,
    statement: string,
    meta: string[],
  ) => (
    <div className="entry" key={id}>
      <button
        type="button"
        className="btn btn--sm btn--ghost"
        aria-pressed={checked.includes(id)}
        onClick={() => {
          if (!pending) toggle(id);
        }}
        disabled={pending}
      >
        {checked.includes(id) ? "已勾" : "勾选"}
      </button>{" "}
      <span className="entry__text">{statement}</span>
      {meta.map((line) => (
        <p className="input__hint" key={line}>
          {line}
        </p>
      ))}
    </div>
  );

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="sim-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="sim-title">
          模拟调研 · 待验证的假设（不是用户数据）
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        <p className="input__hint">
          第 {inquiry.runs} 次模拟（每轮最多 2 次）。下面这些来自模型推演，不是用户说的话。
          要用，先想清楚怎么验证；收下进材料库，下一轮可以带上。
        </p>
        {inquiry.slices.map((slice) => (
          <section key={slice.local_id}>
            <p className="loop__label">
              处境 {slice.local_id}：{slice.situation}（{slice.job}）
            </p>
            <p className="input__hint">
              触发：{slice.trigger}；现在凑合：{slice.workaround}
            </p>
            {inquiry.hypotheses
              .filter((item) => item.slice_id === slice.local_id)
              .map((item) =>
                row(item.local_id, item.statement, [
                  `从哪推的：${item.based_on.length > 0 ? item.based_on.join("、") : "凭空推演（无据）"}`,
                  `怎么被推翻：${item.falsification}`,
                  `需要什么真实证据：${item.evidence_needed}`,
                ]),
              )}
          </section>
        ))}
        {orphans.length > 0 && (
          <section>
            <p className="loop__label">其他（挂不上的假设——后端级联丢条漏了它）</p>
            {orphans.map((item) => row(item.local_id, item.statement, []))}
          </section>
        )}
        {inquiry.unknowns.length > 0 && (
          <section>
            <p className="loop__label">盲区（模拟答不了，只能靠真实接触）</p>
            {inquiry.unknowns.map((item) =>
              row(item.local_id, item.statement, [
                `为什么值得问：${item.why_it_matters}`,
                `怎么去验证：${item.how_to_find_out}`,
              ]),
            )}
          </section>
        )}
      </div>

      <footer className="drawer__foot">
        <div className="row">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => onKeep(checked)}
            disabled={pending || checked.length === 0}
          >
            收下勾选（{checked.length} 条）
          </button>
        </div>
      </footer>
    </aside>
  );
}
