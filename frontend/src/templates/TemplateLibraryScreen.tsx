/* 模板管理屏（`06` §2.3）：把数据层（`useTemplateLibrary`）与两个展示组件接起来。
 *
 * **编辑态是这一屏自己的活状态**，不提到 App：App 只管"当前是哪个顶层视图"，
 * 而"正在编辑哪一份模板"只在模板这一屏里有意义（`06` §3.2 的分工）。
 *
 * **保存的判定顺序**：先问本地 `validateDraft`（只报 UI 级问题：标题空、槽位重名），
 * 再发给后端——后端那些（锚点恰各一、判据 ≥ 1）由界面结构挡住，不在这里复刻。
 * 返回值直接并回列表（`06` §3.2：不推算）。 */

import { useCallback, useState } from "react";

import type { TemplateSectionView } from "../api/types";
import { TemplateEditor } from "../components/TemplateEditor";
import { TemplateListView } from "../components/TemplateListView";
import { blankDraft, toPayload, validateDraft } from "./draft";
import { useTemplateLibrary } from "./useTemplateLibrary";

interface Draft {
  /** null = 新建（尚未有 id）；有值 = 改这份。 */
  editingId: string | null;
  title: string;
  sections: TemplateSectionView[];
}

export function TemplateLibraryScreen({
  active,
  onUse,
}: {
  active: boolean;
  onUse: (templateId: string) => void;
}) {
  const library = useTemplateLibrary(active);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [localProblems, setLocalProblems] = useState<string[]>([]);

  const startBlank = useCallback(() => {
    setLocalProblems([]);
    setDraft({ editingId: null, title: "", sections: blankDraft() });
  }, []);

  const startEditing = useCallback(
    (templateId: string) => {
      const base = library.items.find((item) => item.template_id === templateId);
      if (base === undefined) return;
      setLocalProblems([]);
      setDraft({
        editingId: templateId,
        title: base.title,
        sections: base.sections.map((section) => ({ ...section })),
      });
    },
    [library.items],
  );

  const patchDraft = useCallback(
    (changes: { title?: string; sections?: TemplateSectionView[] }) =>
      setDraft((current) =>
        current === null ? current : { ...current, ...changes },
      ),
    [],
  );

  const save = useCallback(async () => {
    if (draft === null) return;
    const problems = validateDraft(draft.title, draft.sections);
    setLocalProblems(problems);
    if (problems.length > 0) return;

    const payload = toPayload(draft.sections);
    const saved =
      draft.editingId === null
        ? await library.create(draft.title, payload)
        : await library.update(draft.editingId, draft.title, payload);
    // 失败时 `library.error` 已是后端那句中文；留在编辑器里不跳走，让人改了再存
    if (saved !== null) setDraft(null);
  }, [draft, library]);

  // 坐在同一张纸上：别的屏（文档区 / 项目列表）都是 `.doc__inner` 的纸，
  // 模板屏不另起一套容器。
  if (draft === null) {
    return (
      <div className="doc__inner">
      <TemplateListView
        templates={library.items}
        onUse={onUse}
        onEdit={startEditing}
        onDelete={(templateId) => void library.remove(templateId)}
        onNew={startBlank}
        error={library.error}
        pending={library.pending || library.loading}
      />
      </div>
    );
  }

  return (
    <div className="doc__inner">
    <TemplateEditor
      title={draft.title}
      sections={draft.sections}
      baseOptions={library.items}
      onChange={patchDraft}
      onBack={() => setDraft(null)}
      onSave={() => void save()}
      error={
        library.error !== null
          ? library.error
          : localProblems.length > 0
            ? localProblems.join("；")
            : null
      }
      pending={library.pending}
    />
    </div>
  );
}