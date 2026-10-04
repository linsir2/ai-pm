/* 工作区归约：纯函数。事件只是通知，所以这里只做两件事——
   把"这一轮走到的进度"就地记下来，以及给文档打一个"该重读了"的信号。
   真正的数据永远来自后端的投影，这个文件不拼文档。 */

import type {
  DocumentView,
  MemoryEntryView,
  ProjectSummaryView,
  ReadinessItemView,
  RoundView,
  SeatPacketView,
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
  lastSeq: number;
  connection: Connection["state"];
  error: string | null;
  justWritten: string[];
}

export type Action =
  | { type: "loaded"; workspace: WorkspaceView }
  | { type: "round"; round: RoundView }
  | { type: "document"; document: DocumentView }
  | { type: "event"; event: WorkspaceEvent }
  | { type: "connection"; state: Connection["state"] }
  | { type: "error"; message: string | null }
  | { type: "written"; blockIds: string[] }
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

    case "round":
      return {
        ...state,
        round: action.round,
        projectId: state.projectId ?? action.round.project_id,
        roundStale: false,
        error: null,
      };

    case "document":
      return { ...state, document: action.document, documentStale: false };

    case "connection":
      return { ...state, connection: action.state };

    case "error":
      return { ...state, error: action.message };

    case "written":
      return { ...state, justWritten: action.blockIds };

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
    case "packet.emitted":
      return mergePacket(base, event);
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
    default:
      return base;
  }
}

/** 席位交回：把对应那条从"待启动"就地变成"已完成"，闭环区立刻看得到发言。 */
function mergePacket(state: State, event: WorkspaceEvent): State {
  const round = state.round;
  const seatId = event.payload.seat_id;
  if (round === null || typeof seatId !== "string") return state;
  if (event.round_id !== null && event.round_id !== round.round_id) return state;

  const opinions = Array.isArray(event.payload.opinions)
    ? (event.payload.opinions as Record<string, unknown>[]).map((item) => ({
        local_id: String(item.local_id ?? ""),
        form: String(item.form ?? "judgment"),
        about: String(item.about ?? ""),
        claim: String(item.claim ?? ""),
        because: String(item.because ?? ""),
        eids: Array.isArray(item.eids) ? (item.eids as unknown[]).map(String) : [],
        basis_note: item.basis_note === undefined ? null : String(item.basis_note),
        eid: item.eid === undefined || item.eid === null ? null : String(item.eid),
      }))
    : [];

  const packet: SeatPacketView = {
    seat_id: seatId,
    title: titleOf(round, seatId),
    status: "completed",
    pass_index: Number(event.payload.pass_index ?? round.seat_round_count),
    basis: String(event.payload.basis ?? ""),
    opinions,
    judgment_count: opinions.filter((opinion) => opinion.form === "judgment").length,
    question_count: opinions.filter((opinion) => opinion.form === "question").length,
    // §4.4 信封的身份：留痕行按它精确挂载到这一包下面（见 document/dropped.ts）
    packet_id: String(event.payload.packet_id ?? ""),
  };

  const packets = round.seat_packets.map((item) => (item.seat_id === seatId ? packet : item));
  const known = round.seat_packets.some((item) => item.seat_id === seatId);
  return {
    ...state,
    round: { ...round, seat_packets: known ? packets : [...packets, packet] },
  };
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

function titleOf(round: RoundView, seatId: string): string {
  const hit =
    round.seat_packets.find((packet) => packet.seat_id === seatId) ??
    round.nominated_seats.find((seat) => seat.seat_id === seatId);
  return hit === undefined ? seatId : hit.title;
}
