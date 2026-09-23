/* 数据编排：谁去读、谁去写、什么时候重读。全部在这里，组件只管画。

   规矩（与 `05-DDD架构.md` 一致）：事件不推进业务，只负责"该重读了"这个信号；
   真正落进界面的数据永远来自后端的投影。 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AnswersBody, OpeningBody } from "../api/client";
import type { ProjectSummaryView, WorkspaceEvent } from "../api/types";
import { initialState, reduce } from "./workspace";
import { useEvents } from "./useEvents";

export type PendingAction = "start" | "opening" | "answers" | "confirm" | "retry";

const PROJECT_PARAM = "project";

function describe(error: unknown): string {
  return error instanceof ApiError ? `${error.message}（${error.code}）` : String(error);
}

export function useWorkspaceData() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [projects, setProjects] = useState<ProjectSummaryView[]>([]);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const selected = useRef<string | null>(null);

  const loadWorkspace = useCallback(async (projectId: string) => {
    const workspace = await api.workspace(projectId);
    dispatch({ type: "loaded", workspace });
    return workspace;
  }, []);

  const refreshDocument = useCallback(async (projectId: string) => {
    dispatch({ type: "document", document: await api.document(projectId) });
  }, []);

  const selectProject = useCallback(
    (projectId: string) => {
      selected.current = projectId;
      const url = new URL(window.location.href);
      url.searchParams.set(PROJECT_PARAM, projectId);
      window.history.replaceState(null, "", url);
      dispatch({ type: "reset" });
      setBooting(true);
      void loadWorkspace(projectId)
        .catch((error: unknown) => setBootError(describe(error)))
        .finally(() => setBooting(false));
    },
    [loadWorkspace],
  );

  // 启动：至少要有项目才有工作区；没有就停在"建一个项目"那一步。
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const list = await api.listProjects();
        if (!alive) return;
        setProjects(list);
        const wanted = new URLSearchParams(window.location.search).get(PROJECT_PARAM);
        const target = list.find((item) => item.project_id === wanted) ?? list[0];
        if (target !== undefined) {
          selected.current = target.project_id;
          await loadWorkspace(target.project_id);
        }
      } catch (error) {
        if (alive) setBootError(describe(error));
      } finally {
        if (alive) setBooting(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [loadWorkspace]);

  const onEvent = useCallback((event: WorkspaceEvent) => {
    dispatch({ type: "event", event });
  }, []);

  const onReconnect = useCallback(() => {
    const projectId = selected.current;
    if (projectId === null) return;
    void loadWorkspace(projectId).catch((error: unknown) => {
      dispatch({ type: "error", message: describe(error) });
    });
  }, [loadWorkspace]);

  const { connection } = useEvents(onEvent, onReconnect);

  useEffect(() => {
    dispatch({ type: "connection", state: connection });
  }, [connection]);

  // 事件说"文档变了" / "这一轮变了"，就去把真数据读回来。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.documentStale) return;
    void refreshDocument(projectId).catch((error: unknown) => {
      dispatch({ type: "error", message: describe(error) });
    });
  }, [state.documentStale, state.projectId, refreshDocument]);

  useEffect(() => {
    const round = state.round;
    if (round === null || !state.roundStale) return;
    void api
      .getRound(round.round_id)
      .then((fresh) => dispatch({ type: "round", round: fresh }))
      .catch((error: unknown) => dispatch({ type: "error", message: describe(error) }));
  }, [state.roundStale, state.round]);

  /** 跑一个长时间动作：串行、出错只提示、成功就把新的这一轮换进状态。 */
  const run = useCallback(
    async (action: PendingAction, work: () => Promise<void>) => {
      if (pending !== null) return;
      setPending(action);
      dispatch({ type: "error", message: null });
      try {
        await work();
      } catch (error) {
        dispatch({ type: "error", message: describe(error) });
      } finally {
        setPending(null);
      }
    },
    [pending],
  );

  const createProject = useCallback(
    async (title: string) => {
      dispatch({ type: "error", message: null });
      try {
        const summary = await api.createProject(title);
        setProjects((list) => [summary, ...list]);
        selected.current = summary.project_id;
        const url = new URL(window.location.href);
        url.searchParams.set(PROJECT_PARAM, summary.project_id);
        window.history.replaceState(null, "", url);
        await loadWorkspace(summary.project_id);
      } catch (error) {
        dispatch({ type: "error", message: describe(error) });
      }
    },
    [loadWorkspace],
  );

  const startRound = useCallback(
    (input: string) =>
      run("start", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        dispatch({ type: "round", round: await api.startRound(projectId, input) });
      }),
    [run],
  );

  const submitOpening = useCallback(
    (roundId: string, body: OpeningBody) =>
      run("opening", async () => {
        dispatch({ type: "round", round: await api.submitOpening(roundId, body) });
      }),
    [run],
  );

  const submitAnswers = useCallback(
    (roundId: string, body: AnswersBody) =>
      run("answers", async () => {
        dispatch({ type: "round", round: await api.submitAnswers(roundId, body) });
      }),
    [run],
  );

  const confirm = useCallback(
    (roundId: string, accepted: string[]) =>
      run("confirm", async () => {
        const result = await api.confirm(roundId, accepted);
        dispatch({ type: "round", round: result.round });
        const projectId = selected.current;
        if (projectId !== null) await refreshDocument(projectId);
        setProjects((list) =>
          list.map((item) =>
            item.project_id === projectId
              ? { ...item, version_no: result.version_no }
              : item,
          ),
        );
      }),
    [refreshDocument, run],
  );

  const retry = useCallback(
    (roundId: string) =>
      run("retry", async () => {
        dispatch({ type: "round", round: await api.retry(roundId) });
      }),
    [run],
  );

  const dismissError = useCallback(() => dispatch({ type: "error", message: null }), []);

  return {
    state,
    projects,
    booting,
    bootError,
    pending,
    createProject,
    selectProject,
    startRound,
    submitOpening,
    submitAnswers,
    confirm,
    retry,
    dismissError,
    refreshDocument,
  };
}
