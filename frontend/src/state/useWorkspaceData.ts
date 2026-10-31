/* 数据编排：谁去读、谁去写、什么时候重读。全部在这里，组件只管画。

   规矩（与 `05-DDD架构.md` 一致）：事件不推进业务，只负责"该重读了"这个信号；
   真正落进界面的数据永远来自后端的投影。 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AnswersBody, EditBody, OpeningBody } from "../api/client";
import {
  appendSeedText,
  buildExplorationBody,
  buildStart,
  discussionSeedId,
  explorationSeedId,
} from "../api/payload";
import type { ExplorationAnchor } from "../api/payload";
import { cardSeedText } from "./discussions";
import type { PanelStart } from "./discussions";
import { seedText } from "./explorations";
import type {
  EditResult,
  ExplorationView,
  MaterialSummaryView,
  ProjectSummaryView,
  RoundHeadView,
  RoundView,
  SchemaView,
  SearchHitView,
  SearchResultsView,
  ThreadView,
  WorkspaceEvent,
} from "../api/types";
import { setSchema as publishSchema } from "./schema";
import { initialState, reduce } from "./workspace";
import { useEvents } from "./useEvents";

import { pendingBlockedMessage } from "./pending";
import type { PendingAction } from "./pending";

export type { PendingAction } from "./pending";

const PROJECT_PARAM = "project";

function describe(error: unknown): string {
  return error instanceof ApiError ? `${error.message}（${error.code}）` : String(error);
}

export function useWorkspaceData() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [projects, setProjects] = useState<ProjectSummaryView[]>([]);
  // 模型名（`/health`）：只有项目列表的元信息条用它。拿不到就是 null，那一格显示 —。
  const [model, setModel] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  /** 正在决策/恢复的那一条联网申请与被点的动作（只让那颗按钮显示"处理中"）。 */
  const [pendingWebRequest, setPendingWebRequest] = useState<{
    requestId: string;
    decision: "approve" | "reject" | "execute" | "abandon";
  } | null>(null);
  // 现行结构（`GET /schema`）：一次会话拉一次。拉不到不影响主流程，界面回落旧默认值。
  const [schema, setSchemaState] = useState<SchemaView | null>(null);
  // 这个项目走过的轮次（F4）：跟着工作区一起刷新——它只读，事件说"这一轮变了"就该重读。
  const [rounds, setRounds] = useState<RoundHeadView[]>([]);
  // 搜索（F6）：查询与结果都在这里管，防抖 250ms——组件只管画。
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResultsView | null>(null);
  const [searchPending, setSearchPending] = useState(false);
  // 这一轮要带进去的材料（在上传框里选好、还没开轮的那些）
  const [pendingMaterials, setPendingMaterials] = useState<MaterialSummaryView[]>([]);
  //: **材料库**：这个项目里全部材料（与"这一轮带哪些"是两回事）
  const [materials, setMaterials] = useState<MaterialSummaryView[]>([]);
  //: **过目台**：这个项目走过的探索（最新在前）——事件只置 stale，真身从这里读。
  const [explorations, setExplorations] = useState<ExplorationView[]>([]);
  //: **讨论区**：这个项目开过的讨论线程（最新在前）——同样只置 stale，真身从这里读。
  const [discussions, setDiscussions] = useState<ThreadView[]>([]);
  // 底部输入框的文本（上提是为了让"收下为下一轮输入"能往里追加）
  const [roundInput, setRoundInput] = useState("");
  // 收下为"下一轮输入"的候选 id（`探索id#候选id`）：起轮时随请求发出
  const [pendingSeeds, setPendingSeeds] = useState<string[]>([]);
  // 我们自己取消掉的那条在途请求会以网络错误回来——别把它当"连不上后端"弹出来
  const abandoning = useRef(false);
  // 点跨轮编号时临时切过去看的那一轮（只读；"回到最新"就清掉）
  const [historyRound, setHistoryRound] = useState<RoundView | null>(null);
  const selected = useRef<string | null>(null);

  const loadWorkspace = useCallback(async (projectId: string) => {
    const workspace = await api.workspace(projectId);
    // 归属 guard：快速切换时先选的后返回，晚到回包必须丢弃——否则 URL 是 B、
    // 显示是 A、写走的还是 B（写读 `selected.current`）。`selected` 是同步权威，
    // `state.projectId` 在 reset 窗口是 null 会误杀，所以比它。
    if (selected.current !== projectId) return workspace;
    dispatch({ type: "loaded", workspace });
    // 四个只读投影各管各的归属：晚到的不许覆盖新项目（也不许清空它——新 load 拥有 state）。
    const owned = <T,>(apply: (value: T) => void) =>
      (value: T) => {
        if (selected.current === projectId) apply(value);
      };
    const ownedClear = (clear: () => void) => () => {
      if (selected.current === projectId) clear();
    };
    // 轮次列表是**另一个只读投影**（不是工作区的一部分）；它失败不该把工作区打崩
    void api
      .rounds(projectId)
      .then(owned(setRounds))
      .catch(ownedClear(() => setRounds([])));
    // 材料库同理：它是另一个只读投影，失败不该把工作区打崩
    void api
      .materials(projectId)
      .then(owned(setMaterials))
      .catch(ownedClear(() => setMaterials([])));
    // 探索列表（过目台）同理：独立 catch，失败置空不崩工作区
    void api
      .explorations(projectId)
      .then(owned(setExplorations))
      .catch(ownedClear(() => setExplorations([])));
    // 讨论线程列表（讨论 tab）同理。读失败**保留旧列表**并报错：清空会把
    // "后端读不到"伪装成"还没开过讨论"。
    void api
      .discussions(projectId)
      .then(owned(setDiscussions))
      .catch((error: unknown) => {
        if (selected.current === projectId) {
          dispatch({ type: "error", message: describe(error) });
        }
      });
    return workspace;
  }, []);

  const refreshDocument = useCallback(async (projectId: string) => {
    dispatch({ type: "document", document: await api.document(projectId) });
  }, []);

  /**
   * 清掉**属于某个项目**的本地状态：切项目 / 新建项目时调。
   *
   * 为什么必须显式清：这些是 `useState`，不在 reducer 里，`reset` 管不到它们。
   * 不清的后果是**串数据**——上个项目没发出去的输入、待带的种子/材料、搜索词、
   * 只看历史轮，会跟着切到新项目（起轮甚至会把旧项目的种子一起发出去）。
   *
   * 轮次 / 材料 / 探索 / 讨论四个列表也一并清：正常切成功后 `loadWorkspace` 会覆盖，
   * 但它**失败时**（网络断）不会，留着就是上一个项目的数据假扮成这一个的。
   */
  const clearProjectScoped = useCallback(() => {
    setSearchQuery("");
    setSearchResults(null);
    setSearchPending(false);
    setPendingMaterials([]);
    setPendingSeeds([]);
    setRoundInput("");
    setHistoryRound(null);
    setBootError(null);
    setRounds([]);
    setMaterials([]);
    setExplorations([]);
    setDiscussions([]);
  }, []);

  const selectProject = useCallback(
    (projectId: string) => {
      selected.current = projectId;
      const url = new URL(window.location.href);
      url.searchParams.set(PROJECT_PARAM, projectId);
      window.history.replaceState(null, "", url);
      dispatch({ type: "reset" });
      clearProjectScoped();
      setBooting(true);
      void loadWorkspace(projectId)
        .catch((error: unknown) => setBootError(describe(error)))
        .finally(() => setBooting(false));
    },
    [clearProjectScoped, loadWorkspace],
  );

  // 启动：至少要有项目才有工作区；没有就停在"建一个项目"那一步。
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const list = await api.listProjects();
        if (!alive) return;
        setProjects(list);
        // 模型名（项目列表的元信息条要显示）：**与项目列表同一个启动 effect 里并行拿**，
        // 不新增一次串行往返。拿不到就留 null，元信息条那一格显示 —（宁可不显示，也不编）。
        void api
          .health()
          .then((info) => {
            if (alive) setModel(info.model);
          })
          .catch(() => {
            /* 模型名是装饰性的：拿不到不报错、不打断启动 */
          });
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
    const projectId = state.projectId;
    void api
      .getRound(round.round_id)
      .then((fresh) => {
        // 同 loadWorkspace 的归属 guard：切走后的晚到回包不许盖掉新项目。
        if (selected.current !== projectId) return;
        // 同项目内新轮已落地时旧回包也不许倒灌：期望写在动作里，reducer 拿最新 state 比。
        dispatch({ type: "round", round: fresh, expectRoundId: round.round_id });
      })
      .catch((error: unknown) => {
        if (selected.current !== projectId) return;
        dispatch({ type: "error", message: describe(error) });
      });
  }, [state.roundStale, state.round]);

  // `exploration.updated` 只置 stale（事件 payload 只有 id 与状态）：列表真身从这里读回来。
  // 读完必须清信号——否则第二趟调研的完成事件不会再把 effect 触发一遍。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.explorationStale) return;
    void api
      .explorations(projectId)
      .then((list) => {
        if (selected.current === projectId) setExplorations(list);
      })
      .catch((error: unknown) => {
        if (selected.current === projectId) {
          dispatch({ type: "error", message: describe(error) });
        }
      })
      .finally(() => {
        if (selected.current === projectId) dispatch({ type: "explorations" });
      });
  }, [state.explorationStale, state.projectId]);

  // `discussion.updated` 同样只置 stale：线程真身从这里读回来，读完清信号。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.discussionStale) return;
    void api
      .discussions(projectId)
      .then((list) => {
        if (selected.current === projectId) setDiscussions(list);
      })
      .catch((error: unknown) => {
        if (selected.current === projectId) {
          dispatch({ type: "error", message: describe(error) });
        }
      })
      .finally(() => {
        if (selected.current === projectId) dispatch({ type: "discussions" });
      });
  }, [state.discussionStale, state.projectId]);

  /** 跑一个长时间动作：串行、出错只提示、成功就把新的这一轮换进状态。
   *  返回是否真的跑完（busy 被拦 / 失败都是 false），调用方据此决定要不要假装开跑了。 */
  const run = useCallback(
    async (action: PendingAction, work: () => Promise<void>): Promise<boolean> => {
      if (pending !== null) {
        dispatch({ type: "error", message: pendingBlockedMessage(pending) });
        return false;
      }
      setPending(action);
      dispatch({ type: "error", message: null });
      try {
        await work();
        return true;
      } catch (error) {
        if (abandoning.current) return false; // 是我们自己取消的这一轮，不弹错
        dispatch({ type: "error", message: describe(error) });
        return false;
      } finally {
        setPending(null);
      }
    },
    [pending],
  );

  /** 搜索框「带进本轮」：成功后用返回的 RoundView 换掉当前这一轮。 */
  const addHitToRound = useCallback(
    async (hit: SearchHitView) => {
      const round = state.round;
      const materialId = hit.material_id;
      const chunkId = hit.chunk_id ?? hit.ref;
      if (round === null || materialId == null || chunkId === "") return;
      await run("round_material", async () => {
        const fresh = await api.addRoundMaterial(
          round.round_id,
          materialId,
          chunkId,
        );
        dispatch({ type: "round", round: fresh, expectRoundId: round.round_id });
      });
    },
    [run, state.round],
  );

  /**
   * 建项目：成功返回新 summary（调用方凭它决定进不进工作区），失败返回 null。
   *
   * 与 `selectProject` 同序：ref → URL → reset → clear → booting → load。
   * 错误进 `bootError`（两处创建屏只渲染它，没有 toast 可接 `state.error`）。
   */
  const createProject = useCallback(
    async (title: string, templateId?: string): Promise<ProjectSummaryView | null> => {
      dispatch({ type: "error", message: null });
      setBootError(null);
      setBooting(true);
      try {
        const summary = await api.createProject(title, templateId);
        setProjects((list) => [summary, ...list]);
        selected.current = summary.project_id;
        const url = new URL(window.location.href);
        url.searchParams.set(PROJECT_PARAM, summary.project_id);
        window.history.replaceState(null, "", url);
        dispatch({ type: "reset" });
        clearProjectScoped(); // 新项目从干净状态开始（同一个 hook 实例）
        await loadWorkspace(summary.project_id);
        return summary;
      } catch (error) {
        setBootError(describe(error));
        return null;
      } finally {
        setBooting(false);
      }
    },
    [clearProjectScoped, loadWorkspace],
  );

  /** 起轮：输入、材料与调研种子都从本地状态读（输入框已上提到这里）。 */
  const startRound = useCallback(
    () =>
      run("start", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        const input = roundInput.trim();
        if (input === "") return;
        setHistoryRound(null); // 推进当前这一轮的动作，先把历史视图关掉
        dispatch({
          type: "round",
          round: await api.startRound(
            projectId,
            buildStart(input, pendingMaterials, pendingSeeds),
          ),
        });
        // 立刻刷一次轮次列表：不等 SSE——否则新轮会短暂显示成"本轮"、开局时长也拿不到
        void api.rounds(projectId).then(setRounds).catch(() => undefined);
        // **成功才清**（有意修正旧行为：以前一提交就清，失败后用户得重打一遍）。
        setPendingMaterials([]); // 材料已经随这一轮发出去了
        setRoundInput("");
        setPendingSeeds([]);
      }),
    [run, roundInput, pendingMaterials, pendingSeeds],
  );

  /** 收下一条"下一轮输入"：把话并进输入框，并记下种子 id（去重）。候选与卡片共用。 */
  const addSeed = useCallback((seedId: string, text: string) => {
    setRoundInput((current) => appendSeedText(current, text));
    setPendingSeeds((list) => (list.includes(seedId) ? list : [...list, seedId]));
  }, []);

  /** 把后端刚返回的那一趟探索换进列表（没有就补在最前）。 */
  const replaceExploration = useCallback((view: ExplorationView) => {
    setExplorations((list) =>
      list.some((item) => item.exploration_id === view.exploration_id)
        ? list.map((item) => (item.exploration_id === view.exploration_id ? view : item))
        : [view, ...list],
    );
  }, []);

  /** 发起一趟调研：请求内跑完（失败也会返回 status=failed 的那一份）。 */
  const research = useCallback(
    (anchor: ExplorationAnchor) =>
      run("exploration", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        replaceExploration(
          await api.startExploration(projectId, buildExplorationBody(anchor)),
        );
      }),
    [replaceExploration, run],
  );

  /** 项目级研究（P3b-1）：请求内跑完，返回最终投影（done / failed）。
   *  与 M8 调研同一个 `explorations` 数据源，靠 `kind` 区分（研究没有候选）。 */
  const startWebResearch = useCallback(
    (topic: string) =>
      run("exploration", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        replaceExploration(await api.startWebResearch(projectId, topic));
      }),
    [replaceExploration, run],
  );

  /** 把研究来源挂到当前轮：只读快照，成功后整轮重读（来源卡要出现在右栏）。 */
  const attachResearchSources = useCallback(
    (roundId: string, explorationId: string, eids: string[]) =>
      run("research", async () => {
        dispatch({
          type: "round",
          round: await api.attachResearchSources(roundId, explorationId, eids),
          expectRoundId: roundId,
        });
      }),
    [run],
  );

  /** 拿去验证（M13）：拿一张灵感卡的原话起一趟聚焦调研（整份文档 + 补充说明），候选进灵感箱。 */
  const verifyCard = useCallback(
    (statement: string) =>
      run("exploration", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        replaceExploration(
          await api.startExploration(
            projectId,
            buildExplorationBody({ kind: "document", focus: statement }),
          ),
        );
      }),
    [replaceExploration, run],
  );

  const retryExploration = useCallback(
    (explorationId: string) =>
      run("exploration", async () => {
        replaceExploration(await api.retryExploration(explorationId));
      }),
    [replaceExploration, run],
  );

  const abandonExploration = useCallback(
    (explorationId: string) =>
      run("exploration", async () => {
        replaceExploration(await api.abandonExploration(explorationId));
      }),
    [replaceExploration, run],
  );

  const keepCandidate = useCallback(
    (explorationId: string, candidateId: string, as: "seed" | "material") =>
      run("exploration", async () => {
        const view = await api.keepCandidate(explorationId, candidateId, as);
        replaceExploration(view);
        // 成功才入框（M8.1）：失败走 run() 的 banner，文本与种子 id 都不落——
        // 否则框里有话、后端没收下，下次起轮整轮 422 且种子删不掉（无删除 UI）。
        if (as === "seed") {
          const kept = view.candidates.find((item) => item.candidate_id === candidateId);
          if (kept !== undefined) {
            addSeed(explorationSeedId(explorationId, kept.candidate_id), seedText(kept));
          }
        }
      }),
    [replaceExploration, run, addSeed],
  );

  const dismissCandidate = useCallback(
    (explorationId: string, candidateId: string) =>
      run("exploration", async () => {
        replaceExploration(await api.dismissCandidate(explorationId, candidateId));
      }),
    [replaceExploration, run],
  );

  /** 把后端刚返回的那一条线程换进列表（没有就补在最前）。 */
  const replaceThread = useCallback((view: ThreadView) => {
    setDiscussions((list) =>
      list.some((item) => item.thread_id === view.thread_id)
        ? list.map((item) => (item.thread_id === view.thread_id ? view : item))
        : [view, ...list],
    );
  }, []);

  /**
   * 开一个讨论 panel（M9/M18）：主题与名单都已由界面算好（`validatePanel` 兜过底）。
   * 返回新线程 id（失败 null）——界面拿到后自己循环跑 N 轮。
   *
   * `runInquiry`（M13「先跑一轮调研」）：先同步跑一趟聚焦调研（焦点 = 主题）→
   * 候选进灵感箱照常过目；跑成了才开线程，并把趟次 id 当开题底稿带上。
   * 失败不开线程（banner 说原因）——不假装带上了底稿。
   */
  const startPanel = useCallback(
    async (start: PanelStart, runInquiry = false): Promise<string | null> => {
      const projectId = selected.current;
      if (projectId === null) return null;
      let findingExplorationId: string | null = null;
      let thread: ThreadView | null = null;
      const started = await run("discussion", async () => {
        if (runInquiry) {
          const view = await api.startExploration(
            projectId,
            buildExplorationBody({ kind: "document", focus: start.topic }),
          );
          replaceExploration(view);
          if (view.status !== "done") {
            throw new ApiError(
              "exploration_failed",
              `开题调研失败了：${view.failure_reason?.trim() || "后端没留下原因"}`,
            );
          }
          findingExplorationId = view.exploration_id;
        }
        thread = await api.startDiscussion(projectId, {
          topic: start.topic,
          role_ids: start.role_ids,
          question: start.question ?? null,
          finding_exploration_id: findingExplorationId,
        });
        replaceThread(thread);
      });
      if (!started) return null;
      return thread === null ? null : (thread as ThreadView).thread_id;
    },
    [replaceExploration, replaceThread, run],
  );

  /**
   * 跑一轮 panel（M18）：**旁路全局 pending**——N 轮循环由组件驱动，
   * 不能借 `run()` 的串行闸（那会挡住「停止」这类动作）。
   * 失败/被停都返回 null（失败已弹 banner；被停静默）——组件据此收循环。
   */
  const runPanelRound = useCallback(
    async (threadId: string, signal?: AbortSignal): Promise<ThreadView | null> => {
      try {
        const view = await api.runPanelRound(threadId, signal);
        replaceThread(view);
        return view;
      } catch (error) {
        if (error instanceof ApiError && error.code === "aborted") return null;
        dispatch({ type: "error", message: describe(error) });
        return null;
      }
    },
    [replaceThread],
  );

  /** 中途拉人（M18）：下一轮生效；失败弹 banner。 */
  const joinPanel = useCallback(
    (threadId: string, roleId: string) =>
      run("discussion", async () => {
        replaceThread(await api.joinPanel(threadId, roleId));
      }),
    [replaceThread, run],
  );

  /** 停掉在跑的一轮（M18）：服务端也停，不写状态。失败静默（停止不该再报错）。 */
  const abortPanelRun = useCallback((threadId: string) => {
    void api.abortPanelRun(threadId).catch(() => undefined);
  }, []);

  /** 追问一轮：后端成对写入，返回的就是最新的那条线程。 */
  const sayToThread = useCallback(
    (discussionId: string, text: string) =>
      run("discussion", async () => {
        replaceThread(await api.sayToThread(discussionId, text));
      }),
    [replaceThread, run],
  );

  /** 总结成卡：返回的线程里多了 cards。 */
  const assembleCards = useCallback(
    (discussionId: string) =>
      run("discussion", async () => {
        replaceThread(await api.assembleCards(discussionId));
      }),
    [replaceThread, run],
  );

  /**
   * 收下一张卡：`seed` = 预填下一轮输入、`material` = 落材料。
   * 成功才预填（同候选的 M8.1 教训）；失败走 `run()` 的 banner，本地状态不动。
   */
  const keepDiscussionCard = useCallback(
    (discussionId: string, cardId: string, as: "seed" | "material") =>
      run("discussion", async () => {
        const view = await api.keepDiscussionCard(discussionId, cardId, as);
        replaceThread(view);
        if (as === "seed") {
          const kept = view.cards.find((item) => item.card_id === cardId);
          if (kept !== undefined) {
            addSeed(discussionSeedId(discussionId, kept.card_id), cardSeedText(kept));
          }
        }
      }),
    [replaceThread, run, addSeed],
  );

  /** 忽略一张卡：终态——不进灵感箱、不回流。 */
  const dismissDiscussionCard = useCallback(
    (discussionId: string, cardId: string) =>
      run("discussion", async () => {
        replaceThread(await api.dismissDiscussionCard(discussionId, cardId));
      }),
    [replaceThread, run],
  );

  const closeDiscussion = useCallback(
    (discussionId: string) =>
      run("discussion", async () => {
        replaceThread(await api.closeDiscussion(discussionId));
      }),
    [replaceThread, run],
  );

  /** 上传一份材料：成功后挂进"这一轮要带的材料"（还没开轮就能撤掉）。 */
  const addMaterial = useCallback((material: MaterialSummaryView) => {
    setPendingMaterials((list) =>
      list.some((item) => item.material_id === material.material_id) ? list : [...list, material],
    );
    setMaterials((list) =>
      list.some((item) => item.material_id === material.material_id) ? list : [...list, material],
    );
  }, []);

  const removeMaterial = useCallback((materialId: string) => {
    setPendingMaterials((list) => list.filter((item) => item.material_id !== materialId));
  }, []);

  /** 材料库面板上的"带进这一轮 / 不带进这一轮"（只改本轮选择，不动库）。 */
  const toggleMaterial = useCallback(
    (materialId: string) => {
      const found = materials.find((item) => item.material_id === materialId);
      if (found === undefined) return;
      setPendingMaterials((list) =>
        list.some((item) => item.material_id === materialId)
          ? list.filter((item) => item.material_id !== materialId)
          : [...list, found],
      );
    },
    [materials],
  );

  /** 删一份材料：接口回"刷新后的列表"，**整体替换**（不猜、不做乐观更新）。 */
  /** 忘掉一条记忆（M6）：删完刷一次工作台，抽屉与提示词同时更新。 */
  const forgetMemory = useCallback(
    (memoryId: string) =>
      run("memory", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        await api.forgetMemory(projectId, memoryId);
        await refreshDocument(projectId);
      }),
    [run, refreshDocument],
  );

  const deleteMaterial = useCallback(
    (materialId: string) =>
      run("material", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        const list = await api.deleteMaterial(projectId, materialId);
        setMaterials(list);
        // 待选清单里那些已经不在库里的，跟着一起清掉（否则起轮会 422）
        setPendingMaterials((pending) =>
          pending.filter((item) => list.some((item2) => item2.material_id === item.material_id)),
        );
      }),
    [run],
  );

  const reparseMaterial = useCallback(
    (materialId: string) =>
      run("material", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        setMaterials(await api.reparseMaterial(projectId, materialId));
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

  /**
   * 逐条决策联网申请（P3a）：批准即执行，回包就是最新一轮。
   *
   * R17：非工具失败 / 取消时后端不会补发 `round.updated`，客户端收到错误后**主动
   * refetch**，把 `failed` + `result_note` 的真实状态读回来——不伪造事件。
   */
  const decideWebRequest = useCallback(
    (roundId: string, requestId: string, decision: "approve" | "reject") => {
      if (pending !== null) return;
      // 批准会真联网，可能跑几十秒；切走项目后回包不许倒灌到新项目。
      const projectId = selected.current;
      setPendingWebRequest({ requestId, decision });
      void run("web_request", async () => {
        try {
          const fresh = await api.decideWebRequest(roundId, requestId, decision);
          if (selected.current === projectId) {
            dispatch({ type: "round", round: fresh, expectRoundId: roundId });
          }
        } catch (error) {
          if (selected.current !== projectId) return;
          try {
            dispatch({
              type: "round",
              round: await api.getRound(roundId),
              expectRoundId: roundId,
            });
          } catch {
            // 原始错误已经由 run() 的 banner 显示；这里只是补读真相。
          }
          throw error;
        }
      }).finally(() => setPendingWebRequest(null));
    },
    [pending, run],
  );

  /**
   * 超额 approved 的逐条恢复（P3b）：执行一次（临时超额）或放弃（改判 rejected）。
   *
   * 与 decision 同一条纪律：错误后主动 refetch 读回真相；切走项目后回包不许倒灌。
   */
  const recoverWebRequest = useCallback(
    (roundId: string, requestId: string, action: "execute" | "abandon") => {
      if (pending !== null) return;
      const projectId = selected.current;
      setPendingWebRequest({ requestId, decision: action });
      void run("web_request", async () => {
        try {
          const fresh = await api.recoverWebRequest(roundId, requestId, action);
          if (selected.current === projectId) {
            dispatch({ type: "round", round: fresh, expectRoundId: roundId });
          }
        } catch (error) {
          if (selected.current !== projectId) return;
          try {
            dispatch({
              type: "round",
              round: await api.getRound(roundId),
              expectRoundId: roundId,
            });
          } catch {
            // 原始错误已经由 run() 的 banner 显示；这里只是补读真相。
          }
          throw error;
        }
      }).finally(() => setPendingWebRequest(null));
    },
    [pending, run],
  );

  const confirm = useCallback(
    (roundId: string, draftId: string, accepted: string[], acceptedMemory: string[] = []) =>
      run("confirm", async () => {
        const projectId = selected.current;
        let result;
        try {
          result = await api.confirm(roundId, draftId, accepted, acceptedMemory);
        } catch (error) {
          if (selected.current !== projectId) return;
          // 冲突后重读真相；run仍显示原错误，选择由所见draft身份决定是否重置。
          try {
            const fresh = await api.getRound(roundId);
            if (selected.current === projectId) {
              dispatch({ type: "round", round: fresh, expectRoundId: roundId });
            }
            if (projectId !== null) {
              const document = await api.document(projectId);
              if (selected.current === projectId) dispatch({ type: "document", document });
            }
          } catch {
            // 重读失败不覆盖确认的原始错误。
          }
          throw error;
        }
        if (selected.current !== projectId) return;
        setHistoryRound(null);
        dispatch({ type: "round", round: result.round, expectRoundId: roundId });
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

  /** 跑一趟模拟调研：读回的投影里带 artifact，抽屉靠它渲染。 */
  const runSimInquiry = useCallback(
    (roundId: string) =>
      run("simulation", async () => {
        dispatch({ type: "round", round: await api.runSimInquiry(roundId) });
      }),
    [run],
  );

  /** 跑一趟模拟评测（M11）：读回的投影里带报告，抽屉靠它渲染。 */
  const runSimReview = useCallback(
    (roundId: string, personaIds: string[], featureSlugs: string[]) =>
      run("sim_review", async () => {
        dispatch({
          type: "round",
          round: await api.runSimReview(roundId, personaIds, featureSlugs),
        });
      }),
    [run],
  );

  /** 收下勾选为材料：keep 只回小结果，读回整轮刷新勾选态。 */
  const keepSimInquiry = useCallback(
    (roundId: string, chosen: string[]) =>
      run("simulation", async () => {
        await api.keepSimInquiry(roundId, chosen);
        dispatch({ type: "round", round: await api.getRound(roundId) });
      }),
    [run],
  );

  /** 放弃这一轮：不动 pending（那条在途请求由 abandoning 标志位接住）。 */
  const abandon = useCallback(async (roundId: string) => {
    abandoning.current = true;
    dispatch({ type: "error", message: null });
    try {
      // abandon 不进 run() 串行闸：带着期望发，新轮已落地时旧回包由 reducer 丢弃。
      dispatch({
        type: "round",
        round: await api.abandonRound(roundId),
        expectRoundId: roundId,
      });
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
    } finally {
      abandoning.current = false;
    }
  }, []);

  /** 跳过一个还没交回的席位：回包直接进 reducer（与 abandon 同款，不二次重读）。 */
  const skipSeat = useCallback(async (roundId: string, seatId: string) => {
    dispatch({ type: "error", message: null });
    try {
      dispatch({
        type: "round",
        round: await api.skipSeat(roundId, seatId),
        expectRoundId: roundId,
      });
    } catch (error) {
      dispatch({ type: "error", message: describe(error) });
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
    explorations,
    explorationsPending: pending === "exploration",
    research,
    startWebResearch,
    attachResearchSources,
    verifyCard,
    retryExploration,
    abandonExploration,
    keepCandidate,
    dismissCandidate,
    discussions,
    discussionsPending: pending === "discussion",
    startPanel,
    runPanelRound,
    joinPanel,
    abortPanelRun,
    sayToThread,
    assembleCards,
    keepDiscussionCard,
    dismissDiscussionCard,
    closeDiscussion,
    roundInput,
    setRoundInput,
    pendingSeeds,
    addSeed,
    searchQuery,
    searchResults,
    searchPending,
    setSearchQuery,
    addHitToRound,
    pendingMaterials,
    addMaterial,
    removeMaterial,
    materials,
    toggleMaterial,
    deleteMaterial,
    reparseMaterial,
    forgetMemory,
    historyRound,
    projects,
    model,
    booting,
    bootError,
    pending,
    busy: pending !== null,
    createProject,
    selectProject,
    startRound,
    submitOpening,
    submitAnswers,
    pendingWebRequest,
    decideWebRequest,
    recoverWebRequest,
    confirm,
    retry,
    dig,
    runSimInquiry,
    runSimReview,
    keepSimInquiry,
    abandon,
    skipSeat,
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
