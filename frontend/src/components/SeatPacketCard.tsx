import type { SeatPacketView } from "../api/types";
import { opinionFormText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

/** 一个席位交回的信息包：几条判断、几条疑问、依据哪些编号。**没有思维链。** */
export function SeatPacketCard({
  packet,
  order,
  running,
}: {
  packet: SeatPacketView;
  order: number;
  running: boolean;
}) {
  const pending = packet.status !== "completed";
  const stateText = pending ? (running ? "正在交回…" : "待启动") : "已完成";

  return (
    <article
      className={pending ? "packet packet--pending" : "packet"}
      aria-label={`席位 ${packet.title}`}
    >
      <div className="packet__head">
        <span className="packet__index">{String(order).padStart(2, "0")}</span>
        <h3 className="packet__title">{packet.title}</h3>
        <span className={pending && running ? "packet__state packet__state--running" : "packet__state"}>
          {stateText}
        </span>
      </div>

      {!pending && (
        <>
          <p className="packet__basis">依据：{packet.basis.trim() === "" ? "它没写读了什么" : packet.basis}</p>
          <p className="packet__counts">
            <span>判断 {packet.judgment_count} 条</span>
            <span>疑问 {packet.question_count} 个</span>
            <span className="muted">第 {packet.pass_index} 轮</span>
          </p>
          <ul className="opinions">
            {packet.opinions.map((opinion) => (
              <li
                className={`opinion opinion--${opinion.form}`}
                key={opinion.local_id}
                data-eid={opinion.eid ?? undefined}
              >
                <div className="opinion__head">
                  <span className={opinion.form === "question" ? "tag" : "tag tag--decision"}>
                    {opinionFormText(opinion.form)}
                  </span>
                  <span className="opinion__about">{opinion.about}</span>
                </div>
                <span className="opinion__claim">{opinion.claim}</span>
                {opinion.because.trim() !== "" && (
                  <span className="opinion__because">理由：{opinion.because}</span>
                )}
                {opinion.basis_note !== null && (
                  <span className="opinion__because">{opinion.basis_note}</span>
                )}
                <div className="prop__foot">
                  <EvidenceChip eids={opinion.eids} />
                  {opinion.eid !== null && <EvidenceChip eids={[opinion.eid]} />}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}
