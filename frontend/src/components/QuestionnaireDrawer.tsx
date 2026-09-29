/* 问卷抽屉：只覆盖闭环区，文档始终看得见（答题常要对照文档）。
   交卷语义 —— 提交前可改，未答的按跳过交，后果由题面写明。 */

import type { AnswerDraft } from "../api/payload";
import { answerBatchSummary, unansweredCount } from "../api/payload";
import type { DocumentView, QuestionView, RoundView } from "../api/types";
import { QUESTION_KIND_TEXT, QUESTION_TYPE_TEXT, targetText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

export function QuestionnaireDrawer({
  round,
  doc,
  drafts,
  pending,
  onChange,
  onSubmit,
  onSkipAll,
  onCollapse,
}: {
  round: RoundView;
  doc: DocumentView | null;
  drafts: Record<string, AnswerDraft>;
  pending: boolean;
  onChange: (questionId: string, draft: AnswerDraft) => void;
  onSubmit: () => void;
  onSkipAll: () => void;
  onCollapse: () => void;
}) {
  const unanswered = unansweredCount(round, drafts);
  const answered = round.questions.length - unanswered;
  const conflicts = round.questions.filter((question) => question.kind === "conflict").length;
  // 归因对不上号的留痕：新通道里 `source === "assembly"` 的那些
  const unattributed = round.dropped.filter((item) => item.source === "assembly");

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="drawer-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="drawer-title">
          问卷 · 已答 {answered}/{round.questions.length}
        </h2>
        {conflicts > 0 && <span className="tag tag--conflict">{conflicts} 个矛盾要你拍板</span>}
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        {round.answer_sets.length > 0 && (
          <section className="drawer__history" aria-label="本轮前几批你答过的">
            <h3 className="drawer__subtitle">本轮前几批答过的</h3>
            {round.answer_sets.map((batch) => (
              <div className="drawer__batch" key={batch.form_id}>
                <span className="muted small">{answerBatchSummary(batch)}</span>
                {batch.answers.length > 0 && (
                  <ul className="drawer__answers">
                    {batch.answers.map((text) => (
                      <li key={text}>{text}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </section>
        )}
        {unattributed.length > 0 && (
          <p className="drawer__note" aria-label="未能解析的归因">
            有 {unattributed.length} 条席位条目没能对上号（
            {unattributed.map((item) => item.handle).join("、")}
            ），这几条没算作「谁问的」——题目本身没受影响。
          </p>
        )}
        {round.questions.map((question) => (
          <Question
            key={question.question_id}
            question={question}
            doc={doc}
            draft={drafts[question.question_id]}
            onChange={(draft) => onChange(question.question_id, draft)}
          />
        ))}
      </div>

      <footer className="drawer__foot">
        <p className="input__hint">
          {unanswered === 0
            ? "全部答完了。"
            : `未答的 ${unanswered} 题按跳过提交，落到 s11 开放问题，后面还能补。`}
        </p>
        <div className="row">
          <button type="button" className="btn btn--primary" disabled={pending} onClick={onSubmit}>
            {pending ? "正在交卷…" : `交卷（已答 ${answered} 题）`}
          </button>
          <button type="button" className="btn" disabled={pending} onClick={onSkipAll}>
            全部跳过
          </button>
        </div>
      </footer>
    </aside>
  );
}

function Question({
  question,
  doc,
  draft,
  onChange,
}: {
  question: QuestionView;
  doc: DocumentView | null;
  draft: AnswerDraft | undefined;
  onChange: (draft: AnswerDraft) => void;
}) {
  const current: AnswerDraft = draft ?? { selected: [], text: "" };
  const multiple = question.type === "multi";

  const toggle = (optionId: string) => {
    const has = current.selected.includes(optionId);
    const selected = has
      ? current.selected.filter((item) => item !== optionId)
      : multiple
        ? [...current.selected, optionId]
        : [optionId];
    onChange({ ...current, selected, skipped: false });
  };

  return (
    <article className={question.kind === "conflict" ? "q q--conflict" : "q"}>
      <div className="q__head">
        <span className={`tag tag--${question.kind === "conflict" ? "conflict" : "open"}`}>
          {QUESTION_KIND_TEXT[question.kind] ?? question.kind}
        </span>
        <span className="muted small">{QUESTION_TYPE_TEXT[question.type] ?? question.type}</span>
        <span className="muted small mono">{question.question_id}</span>
        <span className="topbar__spacer" />
        <span className="muted small">{targetText(question.target_label, doc?.sections)}</span>
      </div>

      <h3 className="q__title">{question.title}</h3>
      {question.why.trim() !== "" && <p className="q__why">{question.why}</p>}

      {question.options.length > 0 && (
        <div className="options" role={multiple ? "group" : "radiogroup"}>
          {question.options.map(([optionId, text]) => {
            const on = current.selected.includes(optionId);
            return (
              <label className={on ? "opt opt--on" : "opt"} key={optionId}>
                <input
                  type={multiple ? "checkbox" : "radio"}
                  name={question.question_id}
                  checked={on}
                  onChange={() => toggle(optionId)}
                />
                <span>{text}</span>
              </label>
            );
          })}
        </div>
      )}

      <label className="sr-only" htmlFor={`text-${question.question_id}`}>
        补充说明
      </label>
      <textarea
        id={`text-${question.question_id}`}
        className="q__text"
        value={current.text}
        placeholder={question.options.length > 0 ? "补充说明（可空）" : "你的答案"}
        onChange={(event) => onChange({ ...current, text: event.target.value, skipped: false })}
      />

      <p className="q__meta">
        跳过后果：{question.consequence_if_skipped}
        {question.asked_by.length > 0
          ? ` · 谁问的：${question.asked_by.join("、")}`
          : " · 整合者提的（不是某个席位提的）"}
      </p>
      <div className="row">
        <EvidenceChip eids={question.eids} />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onChange({ selected: [], text: "", skipped: true })}
          aria-pressed={current.skipped === true}
        >
          {current.skipped === true ? "已标记跳过" : "跳过这题"}
        </button>
      </div>
    </article>
  );
}
