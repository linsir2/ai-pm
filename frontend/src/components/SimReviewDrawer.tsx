/* 模拟评测抽屉（M11）：**选择器 → 报告**两段。

   - `review === null`：没跑过——选择 persona 与功能（默认值来自 `state/simReview.ts` 的
     纯函数），`pending` 时显示等待；
   - `review !== null`：跑过——三档计数、advisories、逐 persona/功能展开、常驻声明。
   组件不做业务判断：勾选默认值、能不能开始，全在纯函数里（硬规矩 ②）。
   verdict/kind/status 的中文只是**展示词表**，不影响任何判断。 */

import { useState } from "react";

import type {
  DocumentView,
  PersonaSchemaView,
  SimFeatureReviewView,
  SimReviewView,
} from "../api/types";
import {
  canStartReview,
  defaultFeatureSlugs,
  defaultPersonaIds,
  documentFeatures,
  type SimReviewLimits,
} from "../state/simReview";

const VERDICT_TEXT: Record<string, string> = {
  smooth: "顺",
  friction: "有摩擦",
  blocked: "走不通",
};

const KIND_TEXT: Record<string, string> = {
  missing_entry: "缺入口",
  missing_feedback: "缺反馈",
  ambiguous_rule: "规则不清",
  dead_end: "走不通",
  extra_work: "多干活",
  trust_gap: "不敢信",
  edge_break: "边界会炸",
  cannot_start: "开不了头",
};

const OUTCOME_TEXT: Record<string, string> = {
  ok: "能走",
  stuck: "卡住",
  guessed: "只能猜",
};

const CHECK_TEXT: Record<string, string> = {
  verified: "已核对",
  unverified: "文档没说清",
  contradicted: "相矛盾",
};

const DECLARATION = "由 AI 按行为规则走查生成，不是真实用户数据，不能替代用户调研。";

export function SimReviewDrawer({
  review,
  personas,
  document,
  limits,
  pending,
  onRun,
  onClose,
}: {
  /** null = 还没跑过（pending 时显示等待；失败也回到选择器——重按开始即重跑）。 */
  review: SimReviewView | null;
  personas: PersonaSchemaView[];
  document: DocumentView | null;
  limits: SimReviewLimits;
  pending: boolean;
  onRun: (personaIds: string[], featureSlugs: string[]) => void;
  onClose: () => void;
}) {
  const features = documentFeatures(document);
  const [personaIds, setPersonaIds] = useState<string[]>(() =>
    defaultPersonaIds(personas, limits),
  );
  const [featureSlugs, setFeatureSlugs] = useState<string[]>(() =>
    defaultFeatureSlugs(document, limits),
  );

  const toggle = (
    list: string[],
    set: (next: string[]) => void,
    value: string,
  ) =>
    set(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="sim-review-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="sim-review-title">
          模拟评测 · 模拟使用者走查（合成，不是用户数据）
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          收起
        </button>
      </header>
      <div className="drawer__body">
        {review !== null ? (
          <ReviewBody review={review} />
        ) : pending ? (
          <p className="input__hint">
            正在走查（{personaIds.length} 个模拟使用者并行，约半分钟），跑出来自动出现在这里。
          </p>
        ) : (
          <>
            <p className="input__hint">
              选谁去走查、查哪几个功能。走查只读文档，产出的删减建议进草案卡（默认不勾），
              失败会在顶部报错并在草案卡留一行——重按开始即可重跑。
            </p>
            <section className="loop__group">
              <p className="loop__label">模拟使用者（1–{limits.personas} 个）</p>
              {personas.map((persona) => {
                // 勾选行复用开跑卡的 `.seat` 模式（复选框 flex:none + 两行文字）——
                // 文档行的 `.entry` 是 grid，长文本会把复选框那一列压成 0 宽（真 DOM 量到过）
                const on = personaIds.includes(persona.persona_id);
                return (
                  <label className={on ? "seat seat--on" : "seat"} key={persona.persona_id}>
                    <input
                      type="checkbox"
                      name={persona.persona_id}
                      checked={on}
                      onChange={() =>
                        toggle(personaIds, setPersonaIds, persona.persona_id)
                      }
                    />
                    <span>
                      <span className="seat__title">{persona.title}</span>
                      <span className="seat__why">{persona.situation}</span>
                    </span>
                  </label>
                );
              })}
            </section>
            <section className="loop__group">
              <p className="loop__label">要查的功能（1–{limits.features} 个）</p>
              {features.length === 0 && (
                <p className="input__hint">文档里还没有功能——先去草案卡写出一棵功能子树。</p>
              )}
              {features.map((slug) => {
                const on = featureSlugs.includes(slug);
                return (
                  <label className={on ? "seat seat--on" : "seat"} key={slug}>
                    <input
                      type="checkbox"
                      name={slug}
                      checked={on}
                      onChange={() => toggle(featureSlugs, setFeatureSlugs, slug)}
                    />
                    <span>
                      <span className="seat__title">feature#{slug}</span>
                    </span>
                  </label>
                );
              })}
            </section>
            <footer className="drawer__foot">
              <button
                type="button"
                className="btn btn--primary"
                disabled={pending || !canStartReview(personaIds, featureSlugs, limits)}
                onClick={() => onRun(personaIds, featureSlugs)}
              >
                开始评测（{personaIds.length} 人 × {featureSlugs.length} 个功能）
              </button>
            </footer>
          </>
        )}
      </div>
    </aside>
  );
}

function ReviewBody({ review }: { review: SimReviewView }) {
  return (
    <>
      <p className="loop__label">
        {`顺 ${review.summary.smooth} / 有摩擦 ${review.summary.friction} / 走不通 ${review.summary.blocked}`}
        {review.evaluated_version !== null &&
          ` · 基于文档 v${review.evaluated_version} 的走查`}
      </p>
      <p className="input__hint">{DECLARATION}</p>
      {review.advisories.map((item) => (
        <p className="input__hint" key={item}>
          ⚠ {item}
        </p>
      ))}
      {review.personas.map((persona) => (
        <details className="loop__group" key={persona.persona_id}>
          <summary className="loop__label">
            {persona.title}
            {persona.features.length > 0 &&
              `（${persona.features.map((item) => VERDICT_TEXT[item.verdict] ?? item.verdict).join(" / ")}）`}
          </summary>
          {persona.features.map((feature) => (
            <FeatureReport key={feature.feature_slug} feature={feature} />
          ))}
        </details>
      ))}
    </>
  );
}

function FeatureReport({ feature }: { feature: SimFeatureReviewView }) {
  // 注意：**不要**复用 `.entry`——它是"一条条目一行"的 grid（1fr auto），
  // 多段落进去会被逐行摆成两列（真跑截图里挤成窄列叠字过）。这里是普通块流。
  return (
    <section key={feature.feature_slug}>
      <p className="loop__label">
        feature#{feature.feature_slug} · {VERDICT_TEXT[feature.verdict] ?? feature.verdict}
        {!feature.started && "（照文档开不了头）"}
      </p>
      {feature.walkthrough.map((step) => (
        <p className="input__hint" key={step.step_index}>
          第 {step.step_index} 步 {step.action} —— {OUTCOME_TEXT[step.outcome] ?? step.outcome}
          {step.need.trim() !== "" && `：${step.need}`}
        </p>
      ))}
      {feature.frictions.map((item) => (
        <p className="input__hint" key={item.local_id}>
          [{KIND_TEXT[item.kind] ?? item.kind}]{" "}
          {item.entry_id !== "" ? item.entry_id : item.field !== "" ? `字段 ${item.field}` : "功能层面"}
          {" · "}
          {item.note}
          {item.quote.trim() !== "" ? `（原文：「${item.quote}」）` : "（文档没写）"}
          {item.blocks_flow && " · 堵住流程"}
        </p>
      ))}
      {feature.acceptance_checks.map((item) => (
        <p className="input__hint" key={item.local_id}>
          验收 {item.entry_id}：{CHECK_TEXT[item.status] ?? item.status}
          {item.note.trim() !== "" && ` —— ${item.note}`}
        </p>
      ))}
    </section>
  );
}
