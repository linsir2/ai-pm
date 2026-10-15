/* 模板库的数据层（`06` §2.3 / §5）。
 *
 * **不复用 `useWorkspaceData` 的模板那份**：那个来自 `/schema` 的**启动快照**
 * （`schema.templates`，只含内置两份），而管理界面要的是**活的库**——运行时新建/改过/
 * 删掉的都在里面。这两条路要分清，否则刚建好的模板在列表里不出现。
 *
 * **真相源一律从后端读回来**（`06` §3.2）：增删改之后用接口返回的那一份替换本地状态，
 * 不在前端推算（照材料删除/重解析的既有做法）。 */

import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { TemplateSectionPayload, TemplateView } from "../api/types";

export interface TemplateLibrary {
  items: TemplateView[];
  loading: boolean;
  pending: boolean;
  error: string | null;
  reload: () => Promise<void>;
  create: (title: string, sections: TemplateSectionPayload[]) => Promise<TemplateView | null>;
  update: (
    templateId: string,
    title: string,
    sections: TemplateSectionPayload[],
  ) => Promise<TemplateView | null>;
  remove: (templateId: string) => Promise<boolean>;
}

export function useTemplateLibrary(active: boolean): TemplateLibrary {
  const [items, setItems] = useState<TemplateView[]>([]);
  const [loading, setLoading] = useState(active);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api.listTemplates());
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "模板库读不出来");
    } finally {
      setLoading(false);
    }
  }, []);

  // 进这一屏才拉（跟 `useWorkspaceData` 同一套纪律：不要在应用启动时就打这个库）
  useEffect(() => {
    if (active) void reload();
  }, [active, reload]);

  const run = useCallback(async <T,>(task: () => Promise<T>): Promise<T | null> => {
    setPending(true);
    setError(null);
    try {
      return await task();
    } catch (problem) {
      // 后端的消息是中文的、写给人看的，直接上屏（材料层同款）
      setError(problem instanceof Error ? problem.message : "操作没成功");
      return null;
    } finally {
      setPending(false);
    }
  }, []);

  const create = useCallback(
    async (title: string, sections: TemplateSectionPayload[]) =>
      run(async () => {
        const created = await api.createTemplate(title, sections);
        setItems((current) => [...current, created]);
        return created;
      }),
    [run],
  );

  const update = useCallback(
    async (templateId: string, title: string, sections: TemplateSectionPayload[]) =>
      run(async () => {
        const saved = await api.updateTemplate(templateId, title, sections);
        setItems((current) =>
          current.map((item) => (item.template_id === templateId ? saved : item)),
        );
        return saved;
      }),
    [run],
  );

  const remove = useCallback(
    async (templateId: string) =>
      run(async () => {
        await api.deleteTemplate(templateId);
        setItems((current) => current.filter((item) => item.template_id !== templateId));
        return true;
      }).then((done) => done === true),
    [run],
  );

  return { items, loading, pending, error, reload, create, update, remove };
}