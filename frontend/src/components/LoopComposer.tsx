/* 右列 composer（Codex 化）：原来正下方通栏 BottomStrip 的整体搬入 loop 底。
   左边是"这一轮的想法"，右边是这一轮现在能做的动作 —— 视线不出右列。
   逻辑与 BottomStrip 逐字一致（搬的是调用点，不是逻辑）。 */

import type { AwaitingMode } from "../api/payload";
import type { MaterialSummaryView } from "../api/types";
import { MaterialPicker } from "./MaterialPicker";

export function LoopComposer({
  projectId,
  composerMode,
  pending,
  drawerOpen,
  value,
  onChange,
  onStartRound,
  pendingSeedCount = 0,
  materials = [],
  onMaterialUploaded,
  onMaterialRemove,
  libraryCount = 0,
  onOpenLibrary,
  onOpenDrawer,
}: {
  /** 当前项目（上传材料要用它拼 URL）。 */
  projectId: string;
  /** 与旧 BottomStrip 收到的 mode 同一个值（非 null；只读态也照旧可输入）。 */
  composerMode: AwaitingMode;
  pending: string | null;
  drawerOpen: boolean;
  /** 输入文本（受控）：由 `useWorkspaceData` 管，因为"收下为下一轮输入"要往里追加。 */
  value: string;
  onChange: (text: string) => void;
  onStartRound: () => void;
  /** 已经收下、等着随下一轮发出的调研产物条数。 */
  pendingSeedCount?: number;
  /** 这一轮已经选好的材料（上传成功的那些）。 */
  materials?: MaterialSummaryView[];
  onMaterialUploaded?: (material: MaterialSummaryView) => void;
  onMaterialRemove?: (materialId: string) => void;
  /** 材料库里有几份（入口按钮上的数字）。 */
  libraryCount?: number;
  onOpenLibrary?: () => void;
  onOpenDrawer: () => void;
}) {
  // 放弃也是终点：不妨碍开下一轮（这一轮不再占用输入框）
  const accepting =
    composerMode === "input" ||
    composerMode === "done" ||
    composerMode === "failed" ||
    composerMode === "abandoned";
  // opening 模式（2026-10-29 对话流）：上一轮在等你确认复述，但**这里可以起草
  // 下一轮**——输入不禁，只锁提交（一轮未完不能开下一轮是产品规矩）。
  const typable = accepting || composerMode === "opening";
  const busyOpening = composerMode === "opening";
  const canSubmit = accepting && pending === null && value.trim() !== "";

  const submit = () => {
    if (!canSubmit) return;
    onStartRound();
  };

  return (
    <div className="loop__composer">
      <label className="sr-only" htmlFor="round-input">
        这一轮的想法
      </label>
      <textarea
        id="round-input"
        className="input__box"
        value={value}
        readOnly={!typable}
        disabled={!typable}
        placeholder={
          accepting
            ? "把这一轮的想法倒进来：一个新功能、一句要求、一个约束，或者你刚想到的点。"
            : busyOpening
              ? "上一轮还在等复述确认——这里可以先把下一轮的想法起草好。"
              : "这一轮还没结束，先在上面把它走完。"
        }
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") submit();
        }}
      />
      {pendingSeedCount > 0 && (
        <p className="input__hint">来自 {pendingSeedCount} 条调研产物</p>
      )}
      <div className="input__actions">
        {accepting && onMaterialUploaded !== undefined && onMaterialRemove !== undefined && (
          <MaterialPicker
            projectId={projectId}
            materials={materials}
            pending={pending !== null}
            onUploaded={onMaterialUploaded}
            onRemove={onMaterialRemove}
          />
        )}
        {onOpenLibrary !== undefined && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenLibrary}>
            材料库（{libraryCount}）
          </button>
        )}
        <span className="input__hint">
          {accepting
            ? "⌘/Ctrl + Enter 提交"
            : busyOpening
              ? "上面的流会更新；提交确认后再开下一轮。"
              : "一轮走完才能开下一轮。"}
        </span>
        <button className="btn btn--primary" type="button" disabled={!canSubmit} onClick={submit}>
          {pending === "start" ? "正在复述…" : "开始这一轮"}
        </button>
      </div>

      <ComposerActions
        mode={composerMode}
        drawerOpen={drawerOpen}
        onOpenDrawer={onOpenDrawer}
      />
    </div>
  );
}

function ComposerActions({
  mode,
  drawerOpen,
  onOpenDrawer,
}: {
  mode: AwaitingMode;
  drawerOpen: boolean;
  onOpenDrawer: () => void;
}) {
  if (mode === "questionnaire" || mode === "draft") {
    if (drawerOpen) {
      return (
        <div className="row">
          <span className="input__hint">
            {mode === "questionnaire" ? "问卷" : "草案卡"}在右边开着，对照文档看。
          </span>
        </div>
      );
    }
    return (
      <div className="row">
        <button type="button" className="btn btn--primary" onClick={onOpenDrawer}>
          {mode === "questionnaire" ? "打开问卷" : "打开草案卡"}
        </button>
        <span className="input__hint">等你决定。</span>
      </div>
    );
  }

  if (mode === "opening") {
    return (
      <div className="row">
        <span className="input__hint">复述卡在上面，看完点「确认，开跑席位」。</span>
      </div>
    );
  }

  if (mode === "abandoned") {
    return (
      <div className="row">
        <span className="input__hint">
          已放弃，不影响文档；重新输入开下一轮。
        </span>
      </div>
    );
  }

  // failed / loading 的恢复动作收敛到状态行（loop 顶上那一条），这里不再重复入口。
  return null;
}
