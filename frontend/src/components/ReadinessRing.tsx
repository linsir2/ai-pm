export function ReadinessRing({ percent }: { percent: number }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  return (
    <svg
      width="64"
      height="64"
      viewBox="0 0 64 64"
      style={{ flex: "none" }}
      aria-label={`就绪度 ${percent}%`}
      role="img"
    >
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke="var(--border)"
        strokeWidth="6"
      />
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke="var(--success)"
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        transform="rotate(-90 32 32)"
      />
      <text
        x="32"
        y="36"
        textAnchor="middle"
        fill="var(--ink)"
        fontSize="16"
        fontWeight="600"
        fontFamily="var(--font-ui)"
      >
        {percent}%
      </text>
    </svg>
  );
}
