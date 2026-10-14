/* 选段动作条（M17；前身是 M13「就这段聊聊」把手）。
   文档里选中某一条目内的一段 → 浮出两个动作：
   「添加到对话」= 把原文设成讨论锚点、切到讨论页签（开聊卡预填；真开聊仍要点「开聊」，
   入口与动作分开）；
   「调研这段」= 把 `{kind: entry, ref: 所在条目, focus: 选段}` 交给发起抽屉
   （真发请求在抽屉上，与条目「调研」同一条路）。
   全输入设备可用：指针 / 触屏 / 键盘选段都走同一条 `selectionchange`；按钮是原生可聚焦的，
   不再 `aria-hidden`；窄屏不再隐藏（定位数学本来就 clamp 在栏内）。
   跨条目 / 无条目 id / 可编辑区 / 空选段一律不出（ref 会歧义）。 */

import { useEffect, useRef, useState } from "react";

import { handlePlacement } from "../document/selection";
import { useDiscussion } from "./DiscussionContext";
import { useExploration } from "./ExplorationContext";

interface Selection {
  text: string;
  /** 所在条目的 id（`entry-<entry_id>` 派生；发起 entry-scope 调研的 ref）。 */
  ref: string;
  left: number;
  top: number;
}

function elementOf(node: Node | null): Element | null {
  if (node === null) return null;
  return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
}

function isEditable(node: Node | null): boolean {
  return (
    elementOf(node)?.closest("textarea, input, form, [contenteditable='true']") !== null
  );
}

export function SelectionBar({ sectionKey }: { sectionKey: string }) {
  const discussion = useDiscussion();
  const research = useExploration();
  const barRef = useRef<HTMLDivElement | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);

  // 换章就收掉（选区与坐标都换了一套）
  useEffect(() => setSel(null), [sectionKey]);

  useEffect(() => {
    if (!discussion.canStart && !research.canStart) {
      setSel(null);
      return;
    }
    const onSelectionChange = () => {
      const bar = barRef.current;
      const inner = bar?.closest(".doc__inner") ?? null;
      const selection = window.getSelection();
      if (
        bar === null ||
        inner === null ||
        selection === null ||
        selection.isCollapsed ||
        selection.rangeCount === 0
      ) {
        setSel(null);
        return;
      }
      const range = selection.getRangeAt(0);
      if (!inner.contains(range.startContainer) || !inner.contains(range.endContainer)) {
        setSel(null);
        return;
      }
      const startEntry = elementOf(range.startContainer)?.closest(".entry") ?? null;
      const endEntry = elementOf(range.endContainer)?.closest(".entry") ?? null;
      // 单条目内选择，且该条目必须带 id（`entry-<entry_id>`；发起调研的 ref 从这里来）
      if (
        startEntry === null ||
        startEntry !== endEntry ||
        !startEntry.id.startsWith("entry-") ||
        startEntry.id === "entry-"
      ) {
        setSel(null);
        return;
      }
      if (isEditable(range.startContainer) || isEditable(range.endContainer)) {
        setSel(null);
        return;
      }
      const chosen = selection.toString().trim();
      if (chosen === "") {
        setSel(null);
        return;
      }
      const innerRect = inner.getBoundingClientRect();
      const rangeRect = range.getBoundingClientRect();
      setSel({
        text: chosen,
        ref: startEntry.id.slice("entry-".length),
        ...handlePlacement(
          {
            left: rangeRect.left - innerRect.left,
            top: rangeRect.top - innerRect.top,
            right: rangeRect.right - innerRect.left,
            bottom: rangeRect.bottom - innerRect.top,
          },
          { left: 0, top: 0, right: innerRect.width, bottom: innerRect.height },
          { width: bar.offsetWidth || 200, height: bar.offsetHeight || 34 },
        ),
      });
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSel(null);
    };
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [discussion.canStart, research.canStart]);

  if (!discussion.canStart && !research.canStart) return null;
  return (
    <div
      ref={barRef}
      className="selbar"
      role="group"
      aria-label="选段操作"
      hidden={sel === null}
      style={sel === null ? undefined : { left: sel.left, top: sel.top }}
      onMouseDown={(event) => event.preventDefault()} // 保住选区，点击才算数
    >
      <button
        type="button"
        disabled={!discussion.canStart}
        onClick={() => {
          if (sel === null) return;
          discussion.start(sel.text);
          setSel(null);
        }}
      >
        添加到对话
      </button>
      <button
        type="button"
        disabled={!research.canStart}
        onClick={() => {
          if (sel === null) return;
          research.start({ kind: "entry", ref: sel.ref, focus: sel.text });
          setSel(null);
        }}
      >
        调研这段
      </button>
    </div>
  );
}
