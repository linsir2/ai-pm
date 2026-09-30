/* 数据编排：谁去读、谁去写、什么时候重读。全部在这里，组件只管画。

   规矩（与 `05-DDD架构.md` 一致）：事件不推进业务，只负责"该重读了"这个信号；
   真正落进界面的数据永远来自后端的投影。 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AnswersBody, EditBody, OpeningBody } from "../api/client";
import type {
  EditResult,
  ProjectSummaryView,
  RoundHeadView,
  RoundView,
  SchemaView,
  SearchResultsView,
  WorkspaceEvent,
} from "../api/types";
import { setSchema as publishSchema } from "./schema";
import { initialState, reduce } from "./workspace";
import { useEvents } from "./useEvents";

export type PendingAction =
  | "start"
  | "opening"
  | "answers"
  | "confirm"
  | "retry"
  | "dig"
  | "edit";

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
  // 现行结构（`GET /schema`）：一次会话拉一次。拉不到不影响主流程，界面回落旧默认值。
  const [schema, setSchemaState] = useState<SchemaView | null>(null);
  // 这个项目走过的轮次（F4）：跟着工作区一起刷新——它只读，事件说"这一轮变了"就该重读。
  const [rounds, setRounds] = useState<RoundHeadView[]>([]);
  // 搜索（F6）：查询与结果都在这里管，防抖 250ms——组件只管画。
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResultsView | null>(null);
  const [searchPending, setSearchPending] = useState(false);
  // 我们自己取消掉的那条在途请求会以网络错误回来——别把它当"连不上后端"弹出来
  const abandoning = useRef(false);
  // 点跨轮编号时临时切过去看的那一轮（只读；"回到最新"就清掉）
  const [historyRound, setHistoryRound] = useState<RoundView | null>(null);
  const selected = useRef<string | null>(null);

  const loadWorkspace = useCallback(async (projectId: string) => {
    const workspace = await api.workspace(projectId);
    dispatch({ type: "loaded", workspace });
    // 轮次列表是**另一个只读投影**（不是工作区的一部分）；它失败不该把工作区打崩
    void api
      .rounds(projectId)
      .then(setRounds)
      .catch(() => setRounds([]));
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

  useEffect(() => {
    let alive = true;
    void api
      .schema()
      .then((view) => {
        if (!alive) return;
        publishSchema(view);
        setSchemaState(view);
      })
      .catch(() => {
        // 拿不到 schema 不是错误：章节说明与字段中文名回落 `state/schema.ts` 里的旧默认值。
      });
    return () => {
      alive = false;
    };
  }, []);

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

  useEffect(() => {
    const projectId = state.projectId;
    const query = searchQuery.trim();
    if (projectId === null || query === "") {
      setSearchResults(null);
      return;
    }
    let alive = true;
    setSearchPending(true);
    const timer = window.setTimeout(() => {
      void api
        .search(projectId, query)
        .then((found) => {
          if (alive) setSearchResults(found);
        })
        .catch(() => {
          // 搜不到/搜挂了都不是错误：面板上什么都不显示就行
          if (alive) setSearchResults(null);
        })
        .finally(() => {
          if (alive) setSearchPending(false);
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [searchQuery, state.projectId]);

  // 事件说"文档变了" / "这一轮变了"，就去把真数据读回来。
  // 写文档会同时改变就绪度（缺的字段补上了），所以整份工作区一起重读——只刷文档会让
  // 版本条上的"就差 N 项"停在旧数字上。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.documentStale) return;
    void loadWorkspace(projectId).catch((error: unknown) => {
      dispatch({ type: "error", message: describe(error) });
    });
  }, [state.documentStale, state.projectId, loadWorkspace]);

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
        if (abandoning.current) return; // 是我们自己取消的这一轮，不弹错
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
        setHistoryRound(null); // 推进当前这一轮的动作，先把历史视图关掉
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
        setHistoryRound(null);
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

  const dig = useCallback(
    (roundId: string) =>
      run("dig", async () => {
        dispatch({ type: "round", round: await api.dig(roundId) });
      }),
    [run],
  );

  /** 放弃这一轮：不动 pending（那条在途请求由 abandoning 标志位接住）。 */
  const abandon = useCallback(async (roundId: string) => {
    abandoning.current = true;
    dispatch({ type: "error", message: null });
    try {
      dispatch({ type: "round", round: await api.abandonRound(roundId) });
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
    } finally {
      abandoning.current = false;
    }
  }, []);

  /** 手改文档：很快的一次写，但也给"正在写"的反馈，失败要看得见。 */
  const editEntry = useCallback(async (body: EditBody): Promise<EditResult | null> => {
    const projectId = selected.current;
    if (projectId === null) return null;
    setPending("edit");
    dispatch({ type: "error", message: null });
    try {
      const result = await api.editEntry(projectId, body);
      dispatch({ type: "document", document: result.document });
      dispatch({ type: "written", blockIds: result.written_block_ids });
      setProjects((list) =>
        list.map((item) =>
          item.project_id === projectId ? { ...item, version_no: result.version_no } : item,
        ),
      );
      return result;
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
      return null;
    } finally {
      setPending(null);
    }
  }, []);

  const dismissError = useCallback(() => dispatch({ type: "error", message: null }), []);

  /** 提示一句话（查不到编号、回退失败…都走这里；界面不自己吞错误）。 */
  const notify = useCallback(
    (message: string) => dispatch({ type: "error", message }),
    [],
  );

  /** 看历史轮（只读）：点跨轮编号时用。 */
  const viewRound = useCallback(async (roundId: string) => {
    dispatch({ type: "error", message: null });
    try {
      setHistoryRound(await api.getRound(roundId));
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
    }
  }, []);

  const backToLatest = useCallback(() => {
    setHistoryRound(null);
    const projectId = selected.current;
    if (projectId !== null) void loadWorkspace(projectId).catch(() => undefined);
  }, [loadWorkspace]);

  /** 回到某一版：与手改同一条路——后端写完返回整份文档，界面不猜。 */
  const rollback = useCallback(async (versionNo: number) => {
    const projectId = selected.current;
    if (projectId === null) return;
    dispatch({ type: "error", message: null });
    try {
      const result = await api.rollbackVersion(projectId, versionNo);
      dispatch({ type: "document", document: result.document });
      setProjects((list) =>
        list.map((item) =>
          item.project_id === projectId ? { ...item, version_no: result.version_no } : item,
        ),
      );
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
    }
  }, []);

  /** 导出交付用 Markdown：浏览器下载一份 deliverable-v{n}.md。 */
  const exportMarkdown = useCallback(async () => {
    const projectId = selected.current;
    if (projectId === null) return;
    const version = state.document?.version_no ?? state.project?.version_no ?? 1;
    dispatch({ type: "error", message: null });
    try {
      await api.exportMarkdown(projectId, version);
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
    }
  }, [state.document, state.project]);

  return {
    state,
    schema,
    rounds,
    searchQuery,
    searchResults,
    searchPending,
    setSearchQuery,
    historyRound,
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
    dig,
    abandon,
    editEntry,
    rollback,
    viewRound,
    backToLatest,
    notify,
    dismissError,
    refreshDocument,
    exportMarkdown,
  };
}
