/* 草案卡：三组候选逐条要 / 不要。抽屉里**不能改内容**（编辑归文档），
   只有要 / 不要这一个动作；确认后冻结，写入产生新版本。 */

import type {
  DocumentView,
  DraftView,
  MemoryProposalView,
  ProposalView,
  RoundView,
} from "../api/types";
import { allProposalIds, groupAdvisories, NO_PROPOSALS } from "../api/payload";
import { OP_TEXT, ORIGIN_TEXT, targetText } from "../labels";
import { Banner } from "./States";
import { EvidenceChip } from "./EvidenceChip";

export function DraftDrawer({
  round,
  doc,
  accepted,
  memoryAccepted,
  pending,
  onToggle,
  onToggleMemory,
  onSetAccepted,
  onSubmit,
  onDig,
  onSimInquiry,
  onSimReview,
  onOpenTarget,
  onCollapse,
}: {
  round: RoundView;
  doc: DocumentView | null;
  accepted: string[];
  /** 勾中的记忆提议 id（M6）：**默认空**，勾了才随确认写进跨轮记忆。 */
  memoryAccepted: string[];
  pending: boolean;
  onToggle: (proposalId: string) => void;
  onToggleMemory: (proposalId: string) => void;
  onSetAccepted: (proposalIds: string[]) => void;
  onSubmit: () => void;
  /** 草案卡上的"继续深挖"：回到提问，把刚才没问上的题问出来（C6）。 */
  onDig: () => void;
  /** 草案卡上的"去模拟调研"：轮内动作，缺需求信息时去推一轮假设（M10）。 */
  onSimInquiry: () => void;
  /** 草案卡上的"模拟评测"：轮内动作，模拟使用者走查（M11）。 */
  onSimReview: () => void;
  /** 提案的去处可点：跳到文档那一条（传了才出按钮，老消费者不受影响）。 */
  onOpenTarget?: (label: string) => void;
  onCollapse: () => void;
}) {
  const draft: DraftView | null = round.draft;
  if (draft === null) return null;
  const total = draft.proposals.length;
  const memory: MemoryProposalView[] = round.memory_proposals ?? [];
  const advisories = groupAdvisories(round.advisories);

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
        {advisories.length === 1 && (
          <Banner kind="warn" key={advisories[0]!.text}>
            {advisories[0]!.count > 1 && `${advisories[0]!.count} 条 · `}
            跑出本轮去处：{advisories[0]!.text}
          </Banner>
        )}
        {advisories.length > 1 && (
          <details className="q__whybox advisory-fold">
            <summary className="muted small">
              {advisories.length} 类跑出本轮去处的标注（席位替你判断的，确认前看一眼）
            </summary>
            {advisories.map((advisory) => (
              <Banner kind="warn" key={advisory.text}>
                {advisory.count > 1 && `${advisory.count} 条 · `}
                跑出本轮去处：{advisory.text}
              </Banner>
            ))}
          </details>
        )}

        {draft.groups.length === 0 && <p className="muted small">这一轮没有候选。</p>}

        {draft.groups.map(([title, proposals]) => {
          // "它提议的"默认不勾——默认折叠，需要时再展开逐条看
          const foldable =
            proposals.length > 0 &&
            proposals.every((proposal) => proposal.origin_type === "proposed");
          if (!foldable) {
            return (
              <section className="loop__group" key={title}>
                <p className="loop__label">{title}</p>
                {proposals.map((proposal) => (
                  <Proposal
                    key={proposal.proposal_id}
                    proposal={proposal}
                    accepted={accepted.includes(proposal.proposal_id)}
                    doc={doc}
                    onToggle={() => onToggle(proposal.proposal_id)}
                    onOpenTarget={onOpenTarget}
                  />
                ))}
              </section>
            );
          }
          return (
            <details className="loop__group" key={title}>
              <summary className="loop__label">
                {title} · {proposals.length} 条（默认不勾）
              </summary>
              {proposals.map((proposal) => (
                <Proposal
                  key={proposal.proposal_id}
                  proposal={proposal}
                  accepted={accepted.includes(proposal.proposal_id)}
                  doc={doc}
                  onToggle={() => onToggle(proposal.proposal_id)}
                  onOpenTarget={onOpenTarget}
                />
              ))}
            </details>
          );
        })}

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

        {memory.length > 0 && (
          <section className="loop__group">
            <p className="loop__label">这一轮攒下的记忆（{memory.length} 条）</p>
            {memory.map((item) => (
              <MemoryRow
                key={item.proposal_id}
                item={item}
                accepted={memoryAccepted.includes(item.proposal_id)}
                onToggle={() => onToggleMemory(item.proposal_id)}
              />
            ))}
            <p className="input__hint">
              勾了才会记。记忆会影响以后每一轮的判断，所以**默认不勾**；写错一条比不写更麻烦。
            </p>
          </section>
        )}
      </div>

      <footer className="drawer__foot">
        <p className="input__hint">
          已勾 {accepted.length} / {total} 条。确认后写进文档，产生新的一版；没勾的不会写。
          {memory.length > 0 && `  记忆：勾了 ${memoryAccepted.length} / ${memory.length} 条。`}
        </p>
        <div className="row">
          <button type="button" className="btn btn--primary" disabled={pending} onClick={onSubmit}>
            {pending ? "正在写入…" : `确认写入 ${accepted.length} 条`}
          </button>
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
        </div>
        <div className="row drawer__foot-sub">
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={onDig}
            disabled={pending}
          >
            继续深挖（再问一批）
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={onSimInquiry}
            disabled={pending}
          >
            去模拟调研
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={onSimReview}
            disabled={pending}
          >
            模拟评测
          </button>
        </div>
      </footer>
    </aside>
  );
}

const MEMORY_KIND_TEXT: Record<string, string> = {
  lesson: "教训",
  decision: "已定口径",
  rejected: "已否掉",
};

const MEMORY_OP_TEXT: Record<string, string> = {
  add: "记下",
  update: "改一条",
  merge: "合并",
  delete: "删一条",
};

function MemoryRow({
  item,
  accepted,
  onToggle,
}: {
  item: MemoryProposalView;
  accepted: boolean;
  onToggle: () => void;
}) {
  return (
    <article className="prop">
      <div className="prop__side">
        <button
          type="button"
          className={accepted ? "prop__verdict prop__verdict--on" : "prop__verdict"}
          aria-pressed={accepted}
          onClick={onToggle}
        >
          {accepted ? "记" : "不记"}
        </button>
        <span className="prop__target">{MEMORY_OP_TEXT[item.op] ?? item.op}</span>
      </div>
      <div>
        <p className="prop__text">{item.text || "（删掉这条记忆）"}</p>
        <div className="prop__foot">
          <span className="tag">{MEMORY_KIND_TEXT[item.kind] ?? item.kind}</span>
          <EvidenceChip eids={item.refs} />
        </div>
        {item.reason !== "" && (
          <details className="prop__rationalebox">
            <summary className="muted small">为什么记它</summary>
            <p className="prop__rationale">为什么记它：{item.reason}</p>
          </details>
        )}
      </div>
    </article>
  );
}

function Proposal({
  proposal,
  accepted,
  doc,
  onToggle,
  onOpenTarget,
}: {
  proposal: ProposalView;
  accepted: boolean;
  doc: DocumentView | null;
  onToggle: () => void;
  onOpenTarget?: (label: string) => void;
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
          {proposal.simulation_based === true && (
            <span className="tag">模拟评测依据</span>
          )}
          {onOpenTarget !== undefined ? (
            <button
              type="button"
              className="targetlink"
              onClick={() => onOpenTarget(proposal.target_label)}
              title="跳到文档那一条"
            >
              {targetText(proposal.target_label, doc?.sections)}
            </button>
          ) : (
            <span className="muted small">{targetText(proposal.target_label, doc?.sections)}</span>
          )}
          <EvidenceChip eids={proposal.eids} />
          {proposal.kind !== null && <span className="tag">{proposal.kind}</span>}
        </div>
        {proposal.rationale !== null && (
          <details className="prop__rationalebox">
            <summary className="muted small">它为什么这么判断</summary>
            <p className="prop__rationale">它为什么这么判断：{proposal.rationale}</p>
          </details>
        )}
        {proposal.warning !== null && <p className="prop__warn">⚠ {proposal.warning}</p>}
      </div>
    </article>
  );
}
