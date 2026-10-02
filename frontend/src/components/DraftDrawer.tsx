/* 草案卡：三组候选逐条要 / 不要。抽屉里**不能改内容**（编辑归文档），
   只有要 / 不要这一个动作；确认后冻结，写入产生新版本。 */

import type { DocumentView, DraftView, ProposalView, RoundView } from "../api/types";
import { allProposalIds, groupAdvisories, NO_PROPOSALS } from "../api/payload";
import { OP_TEXT, ORIGIN_TEXT, targetText } from "../labels";
import { Banner } from "./States";
import { EvidenceChip } from "./EvidenceChip";

export function DraftDrawer({
  round,
  doc,
  accepted,
  pending,
  onToggle,
  onSetAccepted,
  onSubmit,
  onDig,
  onCollapse,
}: {
  round: RoundView;
  doc: DocumentView | null;
  accepted: string[];
  pending: boolean;
  onToggle: (proposalId: string) => void;
  onSetAccepted: (proposalIds: string[]) => void;
  onSubmit: () => void;
  /** 草案卡上的"继续深挖"：回到提问，把刚才没问上的题问出来（C6）。 */
  onDig: () => void;
  onCollapse: () => void;
}) {
  const draft: DraftView | null = round.draft;
  if (draft === null) return null;
  const total = draft.proposals.length;

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="drawer-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="drawer-title">
          草案 · {total} 条候选
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        {groupAdvisories(round.advisories).map((advisory) => (
          <Banner kind="warn" key={advisory.text}>
            {advisory.count > 1 && `${advisory.count} 条 · `}
            跑出本轮去处：{advisory.text}
          </Banner>
        ))}

        {draft.groups.length === 0 && <p className="muted small">这一轮没有候选。</p>}

        {draft.groups.map(([title, proposals]) => (
          <section className="loop__group" key={title}>
            <p className="loop__label">{title}</p>
            {proposals.map((proposal) => (
              <Proposal
                key={proposal.proposal_id}
                proposal={proposal}
                accepted={accepted.includes(proposal.proposal_id)}
                doc={doc}
                onToggle={() => onToggle(proposal.proposal_id)}
              />
            ))}
          </section>
        ))}

        {draft.gaps.length > 0 && (
          <div className="loop__group">
            <p className="loop__label">写入后仍然空着的必填项</p>
            <ul className="entries">
              {draft.gaps.map((gap) => (
                <li className="entry" key={gap}>
                  <span className="entry__text muted small">{gap}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <footer className="drawer__foot">
        <p className="input__hint">
          已勾 {accepted.length} / {total} 条。确认后写进文档，产生新的一版；没勾的不会写。
        </p>
        <div className="row">
          {/* C4：本批全接受要有，但"它提议的"那组单独计——别让人一条条点 */}
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => onSetAccepted(allProposalIds(draft))}
            disabled={pending || accepted.length === total}
          >
            本批全接受（{total} 条）
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => onSetAccepted(NO_PROPOSALS)}
            disabled={pending || accepted.length === 0}
          >
            全不勾
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={onDig}
            disabled={pending}
          >
            继续深挖（再问一批）
          </button>
          <button type="button" className="btn btn--primary" disabled={pending} onClick={onSubmit}>
            {pending ? "正在写入…" : `确认写入 ${accepted.length} 条`}
          </button>
        </div>
      </footer>
    </aside>
  );
}

function Proposal({
  proposal,
  accepted,
  doc,
  onToggle,
}: {
  proposal: ProposalView;
  accepted: boolean;
  doc: DocumentView | null;
  onToggle: () => void;
}) {
  return (
    <article className={proposal.origin_type === "proposed" ? "prop prop--proposed" : "prop"}>
      <div className="prop__side">
        <button
          type="button"
          className={accepted ? "prop__verdict prop__verdict--on" : "prop__verdict"}
          aria-pressed={accepted}
          onClick={onToggle}
        >
          {accepted ? "要" : "不要"}
        </button>
        <span className="prop__target">{OP_TEXT[proposal.op] ?? proposal.op}</span>
      </div>
      <div>
        <p className="prop__text">{proposal.text}</p>
        <div className="prop__foot">
          <span className="tag">{ORIGIN_TEXT[proposal.origin_type] ?? proposal.origin_type}</span>
          <span className="muted small">{targetText(proposal.target_label, doc?.sections)}</span>
          <EvidenceChip eids={proposal.eids} />
          {proposal.kind !== null && <span className="tag">{proposal.kind}</span>}
        </div>
        {proposal.rationale !== null && (
          <p className="prop__rationale">它为什么这么判断：{proposal.rationale}</p>
        )}
        {proposal.warning !== null && <p className="prop__warn">⚠ {proposal.warning}</p>}
      </div>
    </article>
  );
}
