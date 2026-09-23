import type { ReactNode } from "react";

export function Skeleton({ lines = 4, label = "正在读文档" }: { lines?: number; label?: string }) {
  return (
    <div aria-busy="true" aria-label={label} className="stack" style={{ gap: "var(--space-3)" }}>
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className="skel"
          style={{ height: index === 0 ? 28 : 14, width: index % 3 === 0 ? "100%" : "82%" }}
        />
      ))}
    </div>
  );
}

export function Banner({
  kind = "error",
  children,
}: {
  kind?: "error" | "warn" | "ok";
  children: ReactNode;
}) {
  return (
    <p className={`banner banner--${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}
