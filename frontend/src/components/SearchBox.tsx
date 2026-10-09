/* 顶栏搜索框（F6）：搜这个项目里的条目 / 功能 / 编号 / 席位发言 / 材料块。

   纯展示：查询与结果都由 `useWorkspaceData` 管（防抖 + 调后端在那里），
   这里只负责把字递上去、把点中的那一条递出去。

   四条规矩：
   1. 查询为空（或只有空白）就什么都不弹——后端也不会回全量；
   2. 命中落点（哪一章 / 哪个席位）写在那一行上，人点之前就知道会跳去哪；
   3. 截断了就说明"只显示前 N 条"，不假装全都在；
   4. 键盘可用：↑↓ 在命中间移动（`aria-activedescendant`），Enter 选中的那条，Esc 交回输入框。 */

import { useState } from "react";

import type { SearchHitView, SearchResultsView } from "../api/types";

const KIND_TEXT: Record<string, string> = {
  entry: "文档",
  feature: "功能",
  evidence: "出处",
  opinion: "席位",
  material: "材料",
};

export function SearchBox({
  query,
  results,
  pending,
  onQueryChange,
  onPick,
}: {
  query: string;
  results: SearchResultsView | null;
  pending: boolean;
  onQueryChange: (query: string) => void;
  onPick: (hit: SearchHitView) => void;
}) {
  const [activeIndex, setActiveIndex] = useState(-1);
  const open = query.trim() !== "" && results !== null;
  const hits = results?.hits ?? [];
  const active =
    open && activeIndex >= 0 && activeIndex < hits.length ? hits[activeIndex]! : null;

  return (
    <div className="search">
      <label className="sr-only" htmlFor="project-search">
        搜索
      </label>
      <input
        id="project-search"
        className="search__input"
        type="search"
        placeholder="搜索文档、出处、材料…"
        value={query}
        role="combobox"
        aria-expanded={open}
        aria-controls="project-search-list"
        aria-autocomplete="list"
        aria-activedescendant={active === null ? undefined : `search-opt-${activeIndex}`}
        onChange={(event) => {
          onQueryChange(event.target.value);
          setActiveIndex(-1);
        }}
        onKeyDown={(event) => {
          if (!open || hits.length === 0) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActiveIndex((index) => Math.min(index + 1, hits.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex((index) => Math.max(index - 1, 0));
          } else if (event.key === "Enter" && active !== null) {
            event.preventDefault();
            onPick(active);
          } else if (event.key === "Escape") {
            setActiveIndex(-1);
          }
        }}
      />

      {open && (
        <div className="search__panel">
          {results.hits.length === 0 ? (
            <p className="search__empty muted small">{pending ? "在找…" : "没找到"}</p>
          ) : (
            <ul className="search__hits" id="project-search-list" role="listbox">
              {results.hits.map((hit, index) => (
                <li key={`${hit.kind}-${hit.ref}`} role="presentation">
                  <button
                    type="button"
                    id={`search-opt-${index}`}
                    role="option"
                    aria-selected={index === activeIndex}
                    className={index === activeIndex ? "search__hit search__hit--on" : "search__hit"}
                    onClick={() => onPick(hit)}
                  >
                    <span className="tag">{KIND_TEXT[hit.kind] ?? hit.kind}</span>
                    <span className="search__label">{hit.label}</span>
                    <span className="muted small mono">{hit.where}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {results.truncated && (
            <p className="search__empty muted small">
              只显示前 {results.hits.length} 条，把词写具体一点。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
