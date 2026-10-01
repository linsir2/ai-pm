/* 输入框旁边的材料选择器（工具层 C-2）。

   三件事，按重要性排：
   1. **材料不是你说的话**——界面上明写（提示词里也明写），免得用户以为"传进来就等于我定了"；
  2. 选文件 → 真上传（后端真解析）→ 才进这一轮；上传失败**说人话**（后端的 message）；
   3. **粘贴网址**（P27）→ 后端真抓、真解析 → 同样进这一轮；抓不到也照实说（内网地址 /
      不是网页 / 超时，各是一句人话）；
   4. 列出来的每一份都写着它是谁解析的、有多少块——将来换了 docling，用户看得见区别。 */

import { useRef, useState } from "react";

import { api } from "../api/client";
import type { MaterialSummaryView } from "../api/types";

/** 这份材料是怎么来的（`origin` 由后端在盖章时写死）。认不出的**照实显示原值**，不猜。 */
const ORIGIN_TEXT: Record<string, string> = {
  upload: "上传",
  web_search: "联网检索",
  web_fetch: "抓来的网页",
};

export function MaterialPicker({
  projectId,
  materials,
  pending,
  onUploaded,
  onRemove,
  uploader = api.uploadMaterial,
  fetcher = api.fetchMaterial,
}: {
  projectId: string;
  materials: MaterialSummaryView[];
  pending: boolean;
  onUploaded: (material: MaterialSummaryView) => void;
  onRemove: (materialId: string) => void;
  /** 测试注入口：真实现是 `api.uploadMaterial`（multipart）。 */
  uploader?: (projectId: string, file: File) => Promise<MaterialSummaryView>;
  /** 测试注入口：真实现是 `api.fetchMaterial`（粘贴网址 → 后端去抓）。 */
  fetcher?: (projectId: string, url: string) => Promise<MaterialSummaryView>;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [urlBox, setUrlBox] = useState(false);
  const [url, setUrl] = useState("");
  const [grabbing, setGrabbing] = useState(false);
  const disabled = pending || busy || grabbing;

  const pick = async (file: File | undefined) => {
    if (file === undefined) return;
    setError(null);
    setBusy(true);
    try {
      onUploaded(await uploader(projectId, file));
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
      if (input.current !== null) input.current.value = "";
    }
  };

  const grab = async () => {
    const target = url.trim();
    if (target === "") return;
    setError(null);
    setGrabbing(true);
    try {
      onUploaded(await fetcher(projectId, target));
      setUrl("");
      setUrlBox(false);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setGrabbing(false);
    }
  };

  return (
    <div className="materials">
      <div className="materials__row">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={disabled}
          onClick={() => input.current?.click()}
        >
          {busy ? "正在读…" : "+ 带材料"}
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={disabled}
          aria-expanded={urlBox}
          onClick={() => setUrlBox((open) => !open)}
        >
          + 粘贴网址
        </button>
        <span className="muted small">
          材料是<strong>外部材料</strong>，不是你说的话；它进提示词、不直接写文档
        </span>
        <input
          ref={input}
          className="sr-only"
          type="file"
          aria-label="选择材料"
          onChange={(event) => void pick(event.target.files?.[0])}
        />
      </div>

      {urlBox && (
        <div className="materials__row">
          <input
            className="input__box"
            type="url"
            aria-label="要抓的网址"
            placeholder="粘贴一个网址（http / https 开头）"
            value={url}
            disabled={disabled}
            onChange={(event) => setUrl(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void grab();
            }}
          />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={disabled || url.trim() === ""}
            onClick={() => void grab()}
          >
            {grabbing ? "正在抓…" : "抓取"}
          </button>
        </div>
      )}

      {error !== null && (
        <p className="materials__error" role="alert">
          {error}
        </p>
      )}

      {materials.length > 0 && (
        <ul className="materials__list">
          {materials.map((item) => (
            <li className="materials__item" key={item.material_id}>
              <span className="materials__title">{item.title}</span>
              <span className="muted small">
                来源：{ORIGIN_TEXT[item.origin] ?? item.origin} ·{" "}
                {item.chunk_count} 块 · {item.chars} 字 · {item.parser_id}
                {item.truncated ? " · 已截断" : ""}
                {item.stale ? " · 建议重解析" : ""}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                aria-label={`撤掉 ${item.title}`}
                disabled={pending}
                onClick={() => onRemove(item.material_id)}
              >
                撤掉
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
