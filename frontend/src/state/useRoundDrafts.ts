/* 这一轮的作答现场：复述卡上改过的标签与去处、选了哪些席位、问卷答了什么、草案勾了哪些。

   为什么单独一个 hook：换一轮必须整体重置（上一轮的勾选绝不带进下一轮），
   而"什么时候重置、默认勾什么"只应该在**一个**地方写。

   注意：这些都是界面上的草稿，不是业务状态——能不能提交、提交什么形状，仍由
   后端的投影与 `api/payload.ts` 的纯函数决定。 */

import { useCallback, useEffect, useState } from "react";

import type { AnswerDraft, AwaitingMode, ClaimEdit } from "../api/payload";
import { defaultAccepted, defaultSeats, initialClaimEdits } from "../api/payload";
import type { RoundView } from "../api/types";

export interface DraftSelection {
  round_id: string | null;
  draft_id: string | null;
  accepted: string[];
  memoryAccepted: string[];
}

export interface RoundDrafts {
  answers: Record<string, AnswerDraft>;
  seats: string[];
  /** 复述卡上勾中的联网检索词（旧载体）：**默认不勾**（联网是花钱的事，要显式授权）。 */
  webQueries: string[];
  /** 复述卡上勾中的 analyzer 申请 id（新载体）：同样默认一条都不勾。 */
  approvedRequestIds: string[];
  /** 后续席位 / 整合者申请是否自动批准：默认关。 */
  allowWeb: boolean;
  edits: Record<string, ClaimEdit>;
  accepted: string[];
  /** 草案卡上勾中的**记忆提议**（M6）：**默认一条都不勾**——记忆写错会污染以后每一轮。 */
  memoryAccepted: string[];
  selection: DraftSelection;
  selectionMatches: boolean;
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  changeAnswer: (questionId: string, draft: AnswerDraft) => void;
  toggleSeat: (seatId: string) => void;
  toggleWebQuery: (query: string) => void;
  toggleWebRequest: (requestId: string) => void;
  setAllowWeb: (allow: boolean) => void;
  changeClaim: (claimId: string, edit: ClaimEdit) => void;
  toggleAccepted: (proposalId: string) => void;
  setAccepted: (proposalIds: string[]) => void;
  toggleMemory: (proposalId: string) => void;
}

export function useRoundDrafts(round: RoundView | null, mode: AwaitingMode): RoundDrafts {
  const [answers, setAnswers] = useState<Record<string, AnswerDraft>>({});
  const [seats, setSeats] = useState<string[]>([]);
  const [webQueries, setWebQueries] = useState<string[]>([]);
  const [approvedRequestIds, setApprovedRequestIds] = useState<string[]>([]);
  const [allowWeb, setAllowWebState] = useState(false);
  const [edits, setEdits] = useState<Record<string, ClaimEdit>>({});
  const [selection, setSelection] = useState<DraftSelection>({
    round_id: null, draft_id: null, accepted: [], memoryAccepted: [],
  });
  const [collapsed, setCollapsed] = useState(false);

  const roundId = round?.round_id ?? null;
  const draftId = round?.draft?.draft_id ?? null;

  // 换一轮 = 换现场。**只在轮次 id 变化时重置**：
  // 事件推送与"该重读了"都会换掉 round 对象的引用，但那一轮没变——
  // 之前按 [roundId, round] 重置，结果是每来一条事件就把用户正在打的字清空。
  useEffect(() => {
    setAnswers({});
    // 新轮的决策卡必须重新弹出（旧轮上的"收起"不许带过来）
    setCollapsed(false);
    setSeats(round === null ? [] : defaultSeats(round));
    // 默认一条都不勾：勾了才会真联网（后端也拿这个当唯一判据）
    setWebQueries([]);
    setApprovedRequestIds([]);
    // 自动批准是更宽的授权：换一轮必须重新关掉
    setAllowWebState(false);
    setEdits(round === null ? {} : initialClaimEdits(round));
  }, [roundId]);

  // 抽屉什么时候该自己展开：换轮，或"该交复述 / 该答问卷 / 该勾草案"这件事本身
  // 变了。opening 与问卷/草案同权——新轮的复述卡必须自动弹出（2026-10-29 对话流），
  // 用户手动收起之后，同一 waiting_on 的普通数据刷新不许再把它弹开（mode 不变）。
  useEffect(() => {
    if (mode === "opening" || mode === "questionnaire" || mode === "draft") setCollapsed(false);
  }, [roundId, mode]);

  // 草案一到位，默认勾上"你说过的 + 它替你判断的"（它提议的默认不勾）。
  useEffect(() => {
    setSelection({ round_id: roundId, draft_id: draftId,
      accepted: round?.draft == null ? [] : defaultAccepted(round.draft), memoryAccepted: [] });
  }, [roundId, draftId]);

  const changeAnswer = useCallback((questionId: string, draft: AnswerDraft) => {
    setAnswers((current) => ({ ...current, [questionId]: draft }));
  }, []);

  const toggleSeat = useCallback((seatId: string) => {
    setSeats((current) =>
      current.includes(seatId) ? current.filter((item) => item !== seatId) : [...current, seatId],
    );
  }, []);

  const toggleWebQuery = useCallback((query: string) => {
    setWebQueries((current) =>
      current.includes(query) ? current.filter((item) => item !== query) : [...current, query],
    );
  }, []);

  const toggleWebRequest = useCallback((requestId: string) => {
    setApprovedRequestIds((current) =>
      current.includes(requestId)
        ? current.filter((item) => item !== requestId)
        : [...current, requestId],
    );
  }, []);

  const setAllowWeb = useCallback((allow: boolean) => {
    setAllowWebState(allow);
  }, []);

  const changeClaim = useCallback((claimId: string, edit: ClaimEdit) => {
    setEdits((current) => ({ ...current, [claimId]: edit }));
  }, []);

  const toggleAccepted = useCallback((proposalId: string) => {
    setSelection((current) => current.round_id !== roundId || current.draft_id !== draftId
      ? current : { ...current, accepted: current.accepted.includes(proposalId)
        ? current.accepted.filter((item) => item !== proposalId) : [...current.accepted, proposalId] });
  }, [roundId, draftId]);

  const toggleMemory = useCallback((proposalId: string) => {
    setSelection((current) => current.round_id !== roundId || current.draft_id !== draftId
      ? current : { ...current, memoryAccepted: current.memoryAccepted.includes(proposalId)
        ? current.memoryAccepted.filter((item) => item !== proposalId) : [...current.memoryAccepted, proposalId] });
  }, [roundId, draftId]);

  /** 本批全接受 / 全不勾：一次替换整批，不走逐条 toggle。 */
  const setAcceptedIds = useCallback((proposalIds: string[]) => {
    setSelection((current) => current.round_id !== roundId || current.draft_id !== draftId
      ? current : { ...current, accepted: [...proposalIds] });
  }, [roundId, draftId]);

  return {
    answers,
    seats,
    webQueries,
    approvedRequestIds,
    allowWeb,
    edits,
    accepted: selection.accepted,
    memoryAccepted: selection.memoryAccepted,
    selection,
    selectionMatches: selection.round_id === roundId && selection.draft_id === draftId && draftId !== null,
    collapsed,
    setCollapsed,
    changeAnswer,
    toggleSeat,
    toggleWebQuery,
    toggleWebRequest,
    setAllowWeb,
    changeClaim,
    toggleAccepted,
    setAccepted: setAcceptedIds,
    toggleMemory,
  };
}
