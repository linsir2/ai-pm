import { NewProjectForm } from "./NewProjectForm";
import type { TemplateView } from "../api/types";

/** 一个项目都没有时的第一屏：选模板 + 建项目（与列表页同一份表单，同一套文案口径）。 */
export function Onboarding({
  onCreate,
  error,
  pending,
  templates,
}: {
  onCreate: (title: string, templateId: string) => void;
  error: string | null;
  pending: boolean;
  templates: TemplateView[];
}) {
  return (
    <div className="empty">
      <p className="muted small mono">PM Studio</p>
      <h1 className="empty__title">先建一个项目</h1>
      <p className="empty__text">
        一个项目 = 一份产品文档骨架。骨架按模板来，内容靠一轮一轮长出来：
        你把想法倒进来，系统复述、请领域席位发言、有疑问就出问卷，最后由你在草案卡上勾要哪些，
        勾完才真正写进文档，每写一次多一版。
      </p>
      <NewProjectForm
        templates={templates}
        onCreate={onCreate}
        pending={pending}
        error={error}
        autoFocus
      />
    </div>
  );
}
