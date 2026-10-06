/* 材料库（P19）：项目里**全部**材料 + 三个动作。

   为什么要有它：材料的删除与重解析如果只做后端就是死代码；而且在这之前，
   界面只在"这一轮带什么"那一小撮里看得到材料——重载之后连这个都没有。

   三个动作，措辞刻意分得开：
   - **带进这一轮 / 不带进这一轮**：改的是本轮选择，**不动库**；
   - **重解析**：用当前解析器把原始字节重读一遍（联网来的没有这个按钮——那是网页快照）；
   - **删掉**：只有"没被引用过"的才点得动；用过的那份写着是哪几个编号（编号不能回收）。 */

import type { MaterialSummaryView } from "../api/types";
import { summarizeEids } from "../api/payload";

/** 材料是怎么来的（`origin` 由后端盖章时写死）。认不出的照实显示原值，不猜。 */
const ORIGIN_TEXT: Record<string, string> = {
  upload: "上传",
  web_search: "联网检索",
  web_fetch: "抓来的网页",
  simulation: "模拟调研（模型推演）",
};

export function MaterialLibrary({
  materials,
  selected,
  pending,
  onToggleSelected,
  onDelete,
  onReparse,
  onCollapse,
}: {
  materials: MaterialSummaryView[];
  /** 已经带进这一轮的那些 id。 */
  selected: string[];
  pending: boolean;
  onToggleSelected: (materialId: string) => void;
  onDelete: (materialId: string) => void;
  onReparse: (materialId: string) => void;
  onCollapse: () => void;
}) {
  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="library-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="library-title">
          材料库 · {materials.length} 份
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        <p className="muted small">
          材料是<strong>外部材料</strong>，不是你说的话。被带进某一轮、发过编号的那些会**冻结**：
          编号指向它里面的块，删了编号就指向空气。
        </p>

        {materials.length === 0 ? (
          <p className="muted small">
            还没有材料。可以在输入框旁边「+ 带材料」传一份，或者在复述卡上勾一条联网检索。
          </p>
        ) : (
          <ul className="materials__list">
            {materials.map((item) => {
              const on = selected.includes(item.material_id);
              const frozen = item.eids.length > 0;
              // 被引用过的材料可能挂着几十个编号（真跑里一份材料 31 个 E18…E48）：
              // 原样铺开会把整行撑到几百像素高、把表头挤成竖条（P29 F5）。收几个，其余报数。
              const refs = summarizeEids(item.eids);
              return (
                <li className="materials__item" key={item.material_id}>
                  <span className="materials__title">{item.title}</span>
                  <span className="muted small">
                    来源：{ORIGIN_TEXT[item.origin] ?? item.origin} · {item.chunk_count} 块 ·{" "}
                    {item.chars} 字 · {item.parser_id}
                    {item.truncated ? " · 已截断" : ""}
                    {item.stale ? " · 建议重解析" : ""}
                  </span>
                  <span className="muted small" title={refs.title || undefined}>
                    {frozen
                      ? `已被引用：${refs.shown.join("、")}${
                          refs.rest > 0 ? ` 等 ${refs.rest} 个` : ""
                        }`
                      : "还没被用过（可以删、可以重解析）"}
                  </span>
                  <span className="row">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={pending}
                      onClick={() => onToggleSelected(item.material_id)}
                    >
                      {on ? "不带进这一轮" : "带进这一轮"}
                    </button>
                    {item.origin !== "web_search" && (
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        disabled={pending}
                        onClick={() => onReparse(item.material_id)}
                        title={
                          frozen
                            ? "它已经发过编号：会另出一份用当前解析器读的，旧的留着"
                            : "用当前解析器重新读一遍（原始字节还在，不用重传）"
                        }
                      >
                        重解析
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      aria-label={`删掉 ${item.title}`}
                      disabled={pending || frozen}
                      onClick={() => onDelete(item.material_id)}
                    >
                      删掉
                    </button>
                  </span>
                  {frozen && (
                    <span className="muted small">
                      已经发过编号 {item.eids.join("、")}，不能删——要用新解析器读，就点「重解析」。
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
