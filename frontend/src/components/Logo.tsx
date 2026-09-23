/** 标记：三条长短不一的横线装在一张纸里 —— 文档靠内容生长。纯装饰。 */
export function Logo() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 22 22"
      aria-hidden="true"
      focusable="false"
      className="logo"
    >
      <rect x="1" y="1" width="20" height="20" rx="5" fill="var(--primary)" />
      <g stroke="var(--surface)" strokeWidth="1.6" strokeLinecap="round">
        <line x1="6" y1="7" x2="16" y2="7" />
        <line x1="6" y1="11" x2="16" y2="11" />
        <line x1="6" y1="15" x2="12" y2="15" />
      </g>
    </svg>
  );
}
