/* 点编号回到那一句：滚动 + 闪一下。找不到就返回 false，由调用方决定怎么说话。 */

export function jumpToEvidence(eid: string): boolean {
  const target = document.querySelector<HTMLElement>(`[data-eid="${CSS.escape(eid)}"]`);
  if (target === null) return false;

  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.dataset.flash = "on";
  window.setTimeout(() => {
    delete target.dataset.flash;
  }, 700);
  return true;
}
