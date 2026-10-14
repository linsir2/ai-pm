/* 工作区归约：纯函数。事件只是通知，所以这里只做两件事——
   把"这一轮走到的进度"就地记下来，以及给文档打一个"该重读了"的信号。
   真正的数据永远来自后端的投影，这个文件不拼文档。 */

import type {
  DocumentView,
  MemoryEntryView,
  ProjectSummaryView,
  ReadinessItemView,
  RoundView,
  WorkspaceEvent,
  WorkspaceView,
} from "../api/types";

export interface Connection {
  state: "idle" | "open" | "lost";
}

export interface State {
  projectId: string | null;
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  round: RoundView | null;
  readiness: ReadinessItemView[];
  /** 已确认的跨轮记忆（M6）：记忆抽屉读它；老工作台 payload 没有 → 空数组。 */
  memory: MemoryEntryView[];
  documentStale: boolean;
  roundStale: boolean;
  /** 探索列表（过目台）该重读了。与轮次无关：它由 `exploration.updated` 置起。 */
  explorationStale: boolean;
  /** 讨论线程列表该重读了。与轮次无关：它由 `discussion.updated` 置起（M9）。 */
  discussionStale: boolean;
  lastSeq: number;
  connection: Connection["state"];
  error: string | null;
  justWritten: string[];
}

export type Action =
  | { type: "loaded"; workspace: WorkspaceView }
  | { type: "round"; round: RoundView; expectRoundId?: string }
  | { type: "document"; document: DocumentView }
  | { type: "event"; event: WorkspaceEvent }
  | { type: "connection"; state: Connection["state"] }
  | { type: "error"; message: string | null }
  | { type: "written"; blockIds: string[] }
  /** 探索列表已从后端重读（数据在 `useWorkspaceData` 里）——清掉重读信号。 */
  | { type: "explorations" }
  /** 讨论线程列表已从后端重读（数据在 `useWorkspaceData` 里）——清掉重读信号。 */
  | { type: "discussions" }
  | { type: "reset" };

export const initialState: State = {
  projectId: null,
  project: null,
  document: null,
  round: null,
  readiness: [],
  memory: [],
  documentStale: false,
  roundStale: false,
  explorationStale: false,
  discussionStale: false,
  lastSeq: 0,
  connection: "idle",
  error: null,
  justWritten: [],
};

export function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "loaded":
      return {
        ...state,
        projectId: action.workspace.project.project_id,
        project: action.workspace.project,
        document: action.workspace.document,
        round: action.workspace.round,
        readiness: action.workspace.readiness,
        memory: action.workspace.memory ?? [],
        documentStale: false,
        roundStale: false,
        error: null,
      };

    case "round": {
      // 迟到回包不许倒灌：带期望来的，只落在"现在看的还是当初那一轮"时。
      // 比对必须在这里——reducer 永远看到最新 state，hook 闭包/ref 都会过期。
      if (
        action.expectRoundId !== undefined &&
        state.round?.round_id !== action.expectRoundId
      ) {
        return state;
      }
      return {
        ...state,
        round: action.round,
        projectId: state.projectId ?? action.round.project_id,
        roundStale: false,
        error: null,
      };
    }

    case "document":
      return { ...state, document: action.document, documentStale: false };

    case "connection":
      return { ...state, connection: action.state };

    case "error":
      return { ...state, error: action.message };

    case "written":
      return { ...state, justWritten: action.blockIds };

    case "explorations":
      return { ...state, explorationStale: false };

    case "discussions":
      return { ...state, discussionStale: false };

    case "reset":
      return { ...initialState, connection: state.connection };

    case "event":
      return applyEvent(state, action.event);
  }
}

function applyEvent(state: State, event: WorkspaceEvent): State {
  // 别的项目的事件与当前工作区无关；重复推送的事件也不重复处理。
  if (state.projectId !== null && event.project_id !== state.projectId) return state;
  if (event.seq > 0 && event.seq <= state.lastSeq) return state;

  const base = event.seq > 0 ? { ...state, lastSeq: event.seq } : state;

  switch (event.name) {
    case "packet.emitted": {
      // 铁律④：事件只通知"这一包交回了"，opinions 真源在 GET /round。
      // 这里只把该席位状态从 pending 改成 completed，然后触发重读。
      const round = state.round;
      const seatId = event.payload.seat_id;
      if (round === null || typeof seatId !== "string") return base;
      if (event.round_id !== null && event.round_id !== round.round_id) return base;
      const packets = round.seat_packets.map((item) =>
        item.seat_id === seatId ? { ...item, status: "completed" } : item,
      );
      const known = round.seat_packets.some((item) => item.seat_id === seatId);
      if (!known) return base; // 该席位不在本轮 roster 里，忽略
      return {
        ...base,
        roundStale: true,
        round: { ...round, seat_packets: packets },
      };
    }
    case "round.updated":
      return patchRoundProgress(base, event);
    case "doc.changed": {
      const blockIds = Array.isArray(event.payload.written_block_ids)
        ? (event.payload.written_block_ids as string[]).map(String)
        : [];
      return { ...base, documentStale: true, justWritten: blockIds };
    }
    case "generation.failed":
      return {
        ...base,
        roundStale: true,
        error:
          typeof event.payload.reason === "string" ? event.payload.reason : "这一轮失败了",
      };
    case "exploration.updated":
      // 只置"该重读了"：event payload 只有 id 与状态，列表真身去后端读。
      return { ...base, explorationStale: true };
    case "discussion.updated":
      // 同上（M9）：讨论线程不挂在轮次上，事件只说"有一条变了"。
      return { ...base, discussionStale: true };
    default:
      return base;
  }
}

/** 进度就地从事件上抄（step / 还在等谁 / 第几次席位轮）；要弹什么卡仍以拿到的那份投影为准。 */
function patchRoundProgress(state: State, event: WorkspaceEvent): State {
  const round = state.round;
  if (round === null) return state;
  if (event.round_id !== null && event.round_id !== round.round_id) return state;

  const step = typeof event.payload.step === "string" ? event.payload.step : round.step;
  const waitingOn =
    typeof event.payload.waiting_on === "string" ? event.payload.waiting_on : round.waiting_on;
  const seatRoundCount =
    typeof event.payload.seat_round_count === "number"
      ? event.payload.seat_round_count
      : round.seat_round_count;

  return { ...state, round: { ...round, step, waiting_on: waitingOn, seat_round_count: seatRoundCount } };
}


