/* 数据编排：谁去读、谁去写、什么时候重读。全部在这里，组件只管画。

   规矩（与 `05-DDD架构.md` 一致）：事件不推进业务，只负责"该重读了"这个信号；
   真正落进界面的数据永远来自后端的投影。 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AnswersBody, EditBody, OpeningBody } from "../api/client";
import { appendSeedText, buildExplorationBody, buildStart } from "../api/payload";
import type { ExplorationAnchor } from "../api/payload";
import { resolveRoleId } from "./discussions";
import type { DiscussionAnchor } from "./discussions";
import { seedText } from "./explorations";
import type {
  CandidateView,
  EditResult,
  ExplorationView,
  MaterialSummaryView,
  ProjectSummaryView,
  RoundHeadView,
  RoundView,
  SchemaView,
  SearchResultsView,
  ThreadView,
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
  | "edit"
  /** 材料库上的动作（删 / 重解析）：与"这一轮"无关，但仍然串行，不并发点。 */
  | "material"
  /** 记忆抽屉上的动作（M6）：删一条记忆——同样串行，不与"这一轮"的动作并发。 */
  | "memory"
  /** 过目台上的动作（M8）：发起 / 重试 / 放弃 / 收下 / 删掉，串行不并发。 */
  | "exploration"
  /** 讨论区上的动作（M9）：开聊 / 说 / 总结 / 收卡 / 关掉，同样串行不并发。 */
  | "discussion"
  /** 模拟抽屉上的动作（M10）：跑一趟 / 收下勾选，同样串行不并发。 */
  | "simulation";

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
    dispatch({ type: "loaded", workspace });
    // 轮次列表是**另一个只读投影**（不是工作区的一部分）；它失败不该把工作区打崩
    void api
      .rounds(projectId)
      .then(setRounds)
      .catch(() => setRounds([]));
    // 材料库同理：它是另一个只读投影，失败不该把工作区打崩
    void api
      .materials(projectId)
      .then(setMaterials)
      .catch(() => setMaterials([]));
    // 探索列表（过目台）同理：独立 catch，失败置空不崩工作区
    void api
      .explorations(projectId)
      .then(setExplorations)
      .catch(() => setExplorations([]));
    // 讨论线程列表（讨论 tab）同理
    void api
      .discussions(projectId)
      .then(setDiscussions)
      .catch(() => setDiscussions([]));
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

  // `exploration.updated` 只置 stale（事件 payload 只有 id 与状态）：列表真身从这里读回来。
  // 读完必须清信号——否则第二趟调研的完成事件不会再把 effect 触发一遍。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.explorationStale) return;
    void api
      .explorations(projectId)
      .then(setExplorations)
      .catch((error: unknown) => dispatch({ type: "error", message: describe(error) }))
      .finally(() => dispatch({ type: "explorations" }));
  }, [state.explorationStale, state.projectId]);

  // `discussion.updated` 同样只置 stale：线程真身从这里读回来，读完清信号。
  useEffect(() => {
    const projectId = state.projectId;
    if (projectId === null || !state.discussionStale) return;
    void api
      .discussions(projectId)
      .then(setDiscussions)
      .catch((error: unknown) => dispatch({ type: "error", message: describe(error) }))
      .finally(() => dispatch({ type: "discussions" }));
  }, [state.discussionStale, state.projectId]);

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
        // **成功才清**（有意修正旧行为：以前一提交就清，失败后用户得重打一遍）。
        setPendingMaterials([]); // 材料已经随这一轮发出去了
        setRoundInput("");
        setPendingSeeds([]);
      }),
    [run, roundInput, pendingMaterials, pendingSeeds],
  );

  /** 收下一条候选为"下一轮输入"：把话并进输入框，并记下种子 id（去重）。 */
  const addSeed = useCallback((candidate: CandidateView, explorationId: string) => {
    const seedId = `${explorationId}#${candidate.candidate_id}`;
    setRoundInput((text) => appendSeedText(text, seedText(candidate)));
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
          if (kept !== undefined) addSeed(kept, explorationId);
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

  /** 开一条讨论（M9）：主题与角色都已由界面算好（`resolveRoleId` 兜过底）。 */
  const startDiscussion = useCallback(
    (anchor: DiscussionAnchor) =>
      run("discussion", async () => {
        const projectId = selected.current;
        if (projectId === null) return;
        replaceThread(
          await api.startDiscussion(projectId, {
            topic: anchor.topic,
            role_id: resolveRoleId(anchor.role_id),
            question: null,
          }),
        );
      }),
    [replaceThread, run],
  );

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

  /** 收下一张卡为材料：返回的线程里那张卡的 kept_ref 有值了。 */
  const keepDiscussionCard = useCallback(
    (discussionId: string, cardId: string) =>
      run("discussion", async () => {
        replaceThread(await api.keepDiscussionCard(discussionId, cardId));
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

  const confirm = useCallback(
    (roundId: string, accepted: string[], acceptedMemory: string[] = []) =>
      run("confirm", async () => {
        const result = await api.confirm(roundId, accepted, acceptedMemory);
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

  /** 跑一趟模拟调研：读回的投影里带 artifact，抽屉靠它渲染。 */
  const runSimInquiry = useCallback(
    (roundId: string) =>
      run("simulation", async () => {
        dispatch({ type: "round", round: await api.runSimInquiry(roundId) });
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
    explorations,
    explorationsPending: pending === "exploration",
    research,
    retryExploration,
    abandonExploration,
    keepCandidate,
    dismissCandidate,
    discussions,
    discussionsPending: pending === "discussion",
    startDiscussion,
    sayToThread,
    assembleCards,
    keepDiscussionCard,
    closeDiscussion,
    roundInput,
    setRoundInput,
    pendingSeeds,
    addSeed,
    searchQuery,
    searchResults,
    searchPending,
    setSearchQuery,
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
    runSimInquiry,
    keepSimInquiry,
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
