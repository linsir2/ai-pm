/* 选区把手（M13，D2 设计 A）：文档里选一段 → 浮出「就这段聊聊」。

   三条口径：
   ① **指针专属**（`aria-hidden` + `tabIndex=-1`，与 Medium 划线工具条同策略）——
      键盘/触屏的等价入口是条目工具行的「讨论」按钮（硬要求，D2 原话）；
   ② **绝对定位在 `.doc__inner` 内**：把手跟着内容滚，不漂——D2 原定的"滚动>1屏即消失"
      是因为 fixed 定位会漂，锚进内容后原因消失，砍掉；
   ③ v1 **只支持单条目内选择**；`<980` 由 CSS 隐藏；触屏（coarse pointer）不挂监听。 */

import { useEffect, useRef, useState } from "react";

import { handlePlacement } from "../document/selection";
import { useDiscussion } from "./DiscussionContext";

function elementOf(node: Node | null): Element | null {
  if (node === null) return null;
  return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
}

function isEditable(node: Node | null): boolean {
  return (
    elementOf(node)?.closest("textarea, input, form, [contenteditable='true']") !== null
  );
}

export function DiscussHandle({ sectionKey }: { sectionKey: string }) {
  const discussion = useDiscussion();
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [text, setText] = useState("");
  const [coarse, setCoarse] = useState(false);

  useEffect(() => {
    setCoarse(
      typeof window.matchMedia === "function" &&
        window.matchMedia("(pointer: coarse)").matches,
    );
  }, []);

  // 换章就收掉（选区与坐标都换了一套）
  useEffect(() => setPos(null), [sectionKey]);

  useEffect(() => {
    if (coarse || !discussion.canStart) return;
    const onSelectionChange = () => {
      const button = buttonRef.current;
      const inner = button?.closest(".doc__inner") ?? null;
      const selection = window.getSelection();
      if (
        button === null ||
        inner === null ||
        selection === null ||
        selection.isCollapsed ||
        selection.rangeCount === 0
      ) {
        setPos(null);
        return;
      }
      const range = selection.getRangeAt(0);
      if (!inner.contains(range.startContainer) || !inner.contains(range.endContainer)) {
        setPos(null);
        return;
      }
      const startEntry = elementOf(range.startContainer)?.closest(".entry") ?? null;
      const endEntry = elementOf(range.endContainer)?.closest(".entry") ?? null;
      if (startEntry === null || startEntry !== endEntry) {
        setPos(null); // v1：单条目内选择
        return;
      }
      if (isEditable(range.startContainer) || isEditable(range.endContainer)) {
        setPos(null);
        return;
      }
      const chosen = selection.toString().trim();
      if (chosen === "") {
        setPos(null);
        return;
      }
      const innerRect = inner.getBoundingClientRect();
      const rangeRect = range.getBoundingClientRect();
      setText(chosen);
      setPos(
        handlePlacement(
          {
            left: rangeRect.left - innerRect.left,
            top: rangeRect.top - innerRect.top,
            right: rangeRect.right - innerRect.left,
            bottom: rangeRect.bottom - innerRect.top,
          },
          { left: 0, top: 0, right: innerRect.width, bottom: innerRect.height },
          { width: button.offsetWidth || 96, height: button.offsetHeight || 28 },
        ),
      );
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPos(null);
    };
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [coarse, discussion.canStart]);

  if (coarse) return null;
  return (
    <button
      ref={buttonRef}
      type="button"
      className="discuss-handle"
      aria-hidden="true"
      tabIndex={-1}
      hidden={pos === null}
      style={pos === null ? undefined : { left: pos.left, top: pos.top }}
      onMouseDown={(event) => event.preventDefault()} // 保住选区，点击才算数
      onClick={() => {
        discussion.start(text);
        setPos(null);
      }}
    >
      就这段聊聊
    </button>
  );
}
