/* 这一轮的作答现场：复述卡上改过的标签与去处、选了哪些席位、问卷答了什么、草案勾了哪些。

   为什么单独一个 hook：换一轮必须整体重置（上一轮的勾选绝不带进下一轮），
   而"什么时候重置、默认勾什么"只应该在**一个**地方写。

   注意：这些都是界面上的草稿，不是业务状态——能不能提交、提交什么形状，仍由
   后端的投影与 `api/payload.ts` 的纯函数决定。 */

import { useCallback, useEffect, useState } from "react";

import type { AnswerDraft, AwaitingMode, ClaimEdit } from "../api/payload";
import { defaultAccepted, defaultSeats, initialClaimEdits } from "../api/payload";
import type { RoundView } from "../api/types";

export interface RoundDrafts {
  answers: Record<string, AnswerDraft>;
  seats: string[];
  /** 复述卡上勾中的联网检索词：**默认不勾**（联网是花钱的事，要显式授权）。 */
  webQueries: string[];
  edits: Record<string, ClaimEdit>;
  accepted: string[];
  /** 草案卡上勾中的**记忆提议**（M6）：**默认一条都不勾**——记忆写错会污染以后每一轮。 */
  memoryAccepted: string[];
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  changeAnswer: (questionId: string, draft: AnswerDraft) => void;
  toggleSeat: (seatId: string) => void;
  toggleWebQuery: (query: string) => void;
  changeClaim: (claimId: string, edit: ClaimEdit) => void;
  toggleAccepted: (proposalId: string) => void;
  setAccepted: (proposalIds: string[]) => void;
  toggleMemory: (proposalId: string) => void;
}

export function useRoundDrafts(round: RoundView | null, mode: AwaitingMode): RoundDrafts {
  const [answers, setAnswers] = useState<Record<string, AnswerDraft>>({});
  const [seats, setSeats] = useState<string[]>([]);
  const [webQueries, setWebQueries] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, ClaimEdit>>({});
  const [accepted, setAccepted] = useState<string[]>([]);
  const [memoryAccepted, setMemoryAccepted] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  const roundId = round?.round_id ?? null;
  const draftId = round?.draft?.draft_id ?? null;

  // 换一轮 = 换现场。**只在轮次 id 变化时重置**：
  // 事件推送与"该重读了"都会换掉 round 对象的引用，但那一轮没变——
  // 之前按 [roundId, round] 重置，结果是每来一条事件就把用户正在打的字清空。
  useEffect(() => {
    setAnswers({});
    setAccepted([]);
    setMemoryAccepted([]);
    setSeats(round === null ? [] : defaultSeats(round));
    // 默认一条都不勾：勾了才会真联网（后端也拿这个当唯一判据）
    setWebQueries([]);
    setEdits(round === null ? {} : initialClaimEdits(round));
  }, [roundId]);

  // 抽屉什么时候该自己展开：换轮，或"该答问卷 / 该勾草案"这件事本身变了。
  // 用户手动收起之后，普通的数据刷新不许再把它弹开。
  useEffect(() => {
    if (mode === "questionnaire" || mode === "draft") setCollapsed(false);
  }, [roundId, mode]);

  // 草案一到位，默认勾上"你说过的 + 它替你判断的"（它提议的默认不勾）。
  useEffect(() => {
    if (round?.draft != null) setAccepted(defaultAccepted(round.draft));
  }, [draftId]);

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

  const changeClaim = useCallback((claimId: string, edit: ClaimEdit) => {
    setEdits((current) => ({ ...current, [claimId]: edit }));
  }, []);

  const toggleAccepted = useCallback((proposalId: string) => {
    setAccepted((current) =>
      current.includes(proposalId)
        ? current.filter((item) => item !== proposalId)
        : [...current, proposalId],
    );
  }, []);

  const toggleMemory = useCallback((proposalId: string) => {
    setMemoryAccepted((current) =>
      current.includes(proposalId)
        ? current.filter((item) => item !== proposalId)
        : [...current, proposalId],
    );
  }, []);

  /** 本批全接受 / 全不勾：一次替换整批，不走逐条 toggle。 */
  const setAcceptedIds = useCallback((proposalIds: string[]) => {
    setAccepted([...proposalIds]);
  }, []);

  return {
    answers,
    seats,
    webQueries,
    edits,
    accepted,
    memoryAccepted,
    collapsed,
    setCollapsed,
    changeAnswer,
    toggleSeat,
    toggleWebQuery,
    changeClaim,
    toggleAccepted,
    setAccepted: setAcceptedIds,
    toggleMemory,
  };
}
