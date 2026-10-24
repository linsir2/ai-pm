/* 本轮材料（工具层 C-2）：这一轮真带进提示词的块 + 它们的编号。

   两条规矩：
   1. 只显示**带进去的块**（`RoundView.materials` 就是这么投影的）——没带进去的块没有编号，
      界面也不假装它进过模型；材料卡上写"只带了 N 块 / 共 M 块"。
   2. 编号是可点的出处胶囊（复用 EvidenceChip）：点一下回到那块原文。 */

import type { RoundMaterialView } from "../api/types";
import { materialSource } from "../state/retrieval";
import { EvidenceChip } from "./EvidenceChip";

export function RoundMaterials({
  materials,
  pinnedMaterials = [],
  hasRetrievals = false,
}: {
  materials: RoundMaterialView[];
  pinnedMaterials?: string[];
  /** 本轮有没有检索记录；没有（老轮次）就不画来源徽标，不猜"自动"。 */
  hasRetrievals?: boolean;
}) {
  if (materials.length === 0) return null;

  return (
    <div className="round-materials">
      <p className="muted small">
        这一轮带了 {materials.length} 份材料进提示词（外部材料，不是你说的话）
      </p>
      {materials.map((material) => (
        <details className="round-materials__item" key={material.material_id}>
          <summary>
            <span className="round-materials__title">{material.title}</span>
            <span className="muted small">
              {material.chunks.length} / {material.chunk_count} 块 · {material.parser_id}
              {material.origin === "web_search" ? " · 联网检索" : ""}
            </span>
            {materialSource(
              material.material_id,
              pinnedMaterials,
              hasRetrievals,
            ) !== null && (
              <span className="tag round-materials__source">
                {materialSource(
                  material.material_id,
                  pinnedMaterials,
                  hasRetrievals,
                )}
              </span>
            )}
          </summary>
          {material.warnings.map((warning) => (
            <p className="muted small" key={warning}>
              {warning}
            </p>
          ))}
          <ol className="round-materials__chunks">
            {material.chunks.map((chunk) => (
              // `data-chunk` 是搜索结果里"材料命中"的跳转落点（`jumpToChunk`）：
              // 同一时刻只会有一处材料卡在页面上（复述卡或闭环区，不是两个都画）
              <li key={chunk.chunk_id} data-chunk={chunk.chunk_id}>
                <span className="muted small mono">{chunk.anchor}</span>
                <EvidenceChip eids={[chunk.eid]} />
                <p className="round-materials__text">{chunk.text}</p>
              </li>
            ))}
          </ol>
        </details>
      ))}
    </div>
  );
}
