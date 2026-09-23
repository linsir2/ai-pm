/* 这一轮的作答现场：复述卡上改过的标签与去处、选了哪些席位、问卷答了什么、草案勾了哪些。

   为什么单独一个 hook：换一轮必须整体重置（上一轮的勾选绝不带进下一轮），
   而"什么时候重置、默认勾什么"只应该在**一个**地方写。

   注意：这些都是界面上的草稿，不是业务状态——能不能提交、提交什么形状，仍由
   后端的投影与 `api/payload.ts` 的纯函数决定。 */

import { useCallback, useEffect, useState } from "react";

import type { AnswerDraft, ClaimEdit } from "../api/payload";
import { defaultAccepted, defaultSeats, initialClaimEdits } from "../api/payload";
import type { RoundView } from "../api/types";

export interface RoundDrafts {
  answers: Record<string, AnswerDraft>;
  seats: string[];
  edits: Record<string, ClaimEdit>;
  accepted: string[];
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  changeAnswer: (questionId: string, draft: AnswerDraft) => void;
  toggleSeat: (seatId: string) => void;
  changeClaim: (claimId: string, edit: ClaimEdit) => void;
  toggleAccepted: (proposalId: string) => void;
}

export function useRoundDrafts(round: RoundView | null): RoundDrafts {
  const [answers, setAnswers] = useState<Record<string, AnswerDraft>>({});
  const [seats, setSeats] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, ClaimEdit>>({});
  const [accepted, setAccepted] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  const roundId = round?.round_id ?? null;
  const draftId = round?.draft?.draft_id ?? null;

  // 换一轮 = 换现场。
  useEffect(() => {
    setAnswers({});
    setAccepted([]);
    setCollapsed(false);
    setSeats(round === null ? [] : defaultSeats(round));
    setEdits(round === null ? {} : initialClaimEdits(round));
  }, [roundId, round]);

  // 草案一到位，默认勾上"你说过的 + 它替你判断的"（它提议的默认不勾）。
  useEffect(() => {
    if (round?.draft != null) setAccepted(defaultAccepted(round.draft));
  }, [draftId, round]);

  const changeAnswer = useCallback((questionId: string, draft: AnswerDraft) => {
    setAnswers((current) => ({ ...current, [questionId]: draft }));
  }, []);

  const toggleSeat = useCallback((seatId: string) => {
    setSeats((current) =>
      current.includes(seatId) ? current.filter((item) => item !== seatId) : [...current, seatId],
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

  return {
    answers,
    seats,
    edits,
    accepted,
    collapsed,
    setCollapsed,
    changeAnswer,
    toggleSeat,
    changeClaim,
    toggleAccepted,
  };
}
