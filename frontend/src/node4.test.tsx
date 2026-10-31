// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { useWorkspaceData } from "./state/useWorkspaceData";
import type { DocumentView, ProposalView, RoundView, WorkspaceView } from "./api/types";

// 只替换外部SSE/HTTP传输，App、hook、client、卡片、payload都是真消费者。
class TestEvents extends EventTarget {
  static sources: TestEvents[] = [];
  onopen = null;
  onerror = null;
  close() {}
  constructor() { super(); TestEvents.sources.push(this); }
}
const item: ProposalView = {
  proposal_id: "p01", entry_id: "old#01", op: "replace", target_label: "s5/order.main_flow",
  text: "新流程", origin_type: "proposed", eids: ["E2"], rationale: null, kind: "steps", warning: null,
  base_version_no: 2, before: { entry_id: "old#01", text: "旧流程", refs: ["E1"] }, requires_review: true,
};
const block = { block_id: "flow", kind: "steps", feature_slug: null,
  field: "main_flow", field_title: "主流程", multiple: true, manually_edited: false, children: [], entries: [
    { entry_id: "other#01", text: "当前正文", refs: ["E1"] },
    { entry_id: "old#01", text: "当前正文", refs: ["E1"] },
  ] };
const doc: DocumentView = {
  document_id: "doc", project_id: "p1", title: "节点4", version_no: 2, preference_entries: [],
  sections: [ { section_id: "s1", title: "背景", blocks: [], role: "context" },
    { section_id: "s5", title: "功能", role: "feature", blocks: [
      { block_id: "order", kind: "feature", feature_slug: "order", field: null,
        field_title: null, multiple: true, manually_edited: false, entries: [], children: [block] },
    ] },
  ],
};
function round(draft_id = "d-old", round_id = "r-seen"): RoundView {
  return {
    round_id, project_id: "p1", user_input: "修订流程", step: "awaiting_confirm", waiting_on: "draft_card",
    seat_round_count: 1, seats: [], active_seats: [], nominated_seats: [], available_seats: [], claims: [],
    seat_packets: [], dropped: [], questions: [], asked_questions: [], list_items: [],
    draft: { draft_id, groups: [["它提议的", [item]]], proposals: [item], gaps: [] },
    advisories: [], failure_reason: null, failure_at_step: null, retry_from: null, abandon_reason: null,
    confirmed_version_no: null, form_id: null, analysis_basis: "", web_requests: [],
    answer_sets: [], materials: [], web_queries: [], research_sources: [],
    allow_web: false, sim_inquiry: null, sim_review: null, web_search_available: false,
    web_search_network_access: false, retrievals: [], pinned_materials: [], integration_seq: 0, manual_chunks: [],
    memory_proposals: [{ proposal_id: "mem#1", op: "add", kind: "decision", text: `记忆${draft_id}`,
      refs: ["E2"], reason: "用户修订", target_id: null }],
  };
}
function roundWith(extra: ProposalView[], draft_id = "d-old", round_id = "r-seen"): RoundView {
  const base = round(draft_id, round_id);
  const proposals = [...base.draft!.proposals, ...extra];
  return { ...base, draft: { ...base.draft!, proposals, groups: [["它提议的", proposals]], gaps: [] } };
}
let current: RoundView;
let documentView: DocumentView;
let posts: { path: string; body: Record<string, unknown> }[];
let conflict: boolean;
let reads: string[];
function ws(): WorkspaceView {
  return { project: { project_id: "p1", title: "节点4", version_no: 2, updated_at: "2026-10-30",
    template_id: "standard", template_title: "标准" }, document: documentView, round: current, readiness: [], memory: [] };
}
beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  current = round(); documentView = structuredClone(doc); posts = []; reads = []; conflict = false;
  TestEvents.sources = [];
  vi.stubGlobal("EventSource", TestEvents);
  vi.stubGlobal("CSS", { escape: (text: string) => text });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  vi.stubGlobal("fetch", async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    reads.push(path);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)); posts.push({ path, body });
      if (path.endsWith("/confirm")) {
        if (conflict) return new Response(JSON.stringify({ code: "proposal_stale", message: "正文已变，请重新过目" }), { status: 409 });
        return new Response(JSON.stringify({ version_no: 3, round: { ...current, step: "done", waiting_on: "none" } }));
      }
    }
    const value = path.endsWith("/auth/me") ? { user_id: "u1", username: "user", is_admin: false }
      : path.endsWith("/health") ? { model: "test" }
      : path.endsWith("/schema") ? null
      : path === "/api/projects" ? [ws().project]
      : path.endsWith("/workspace") ? ws()
      : path.endsWith("/document") ? documentView
      : path.endsWith("/rounds") ? [{ round_id: "r-history", step: "awaiting_confirm", created_at: "2026-10-29", user_input: "历史修订", claims_count: 0, questions_count: 0, confirmed_version_no: null, claim_headlines: [] }]
      : path === "/api/rounds/r-history" ? round("d-history", "r-history")
      : path.endsWith("/calls") ? { round_id: current.round_id, calls: [], model_calls: 0, model_failed: 0, tool_calls: 0, tool_failed: 0 }
      : path.startsWith("/api/rounds/") ? current : [];
    return new Response(JSON.stringify(value));
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("真实confirm POST保留所见draft与文档/记忆IDs，不从刷新后的round补身份", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await waitFor(() => expect(result.current.state.round?.round_id).toBe("r-seen"));
  current = round("d-new");
  await act(async () => { await result.current.confirm("r-seen", "d-old", ["p01"], ["mem#1"]); });
  expect(posts[0]).toEqual({ path: "/api/rounds/r-seen/confirm", body: {
    draft_id: "d-old", accepted: ["p01"], accepted_memory: ["mem#1"],
  } });
});

it("409后重读Round/Doc、保留错误，不假装confirm成功", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await waitFor(() => expect(result.current.state.round?.round_id).toBe("r-seen"));
  conflict = true;
  current = round("d-new");
  await act(async () => { await result.current.confirm("r-seen", "d-old", ["p01"], ["mem#1"]); });
  expect(result.current.state.round?.draft?.draft_id).toBe("d-new");
  expect(result.current.state.round?.step).toBe("awaiting_confirm");
  expect(result.current.state.document?.version_no).toBe(2);
  expect(result.current.state.error).toContain("正文已变");
  expect(reads).toContain("/api/rounds/r-seen");
  expect(reads).toContain("/api/projects/p1/document");
});

it("App从显示卡确认真实round/draft/doc+memory；历史待确认卡只读", async () => {
  render(<App />);
  await screen.findByText("记忆d-old");
  await waitFor(() => expect(screen.getByRole("button", { name: /确认写入/ }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByText(/它提议的 · 1 条/));
  fireEvent.click(screen.getByRole("button", { name: "不要" }));
  fireEvent.click(screen.getByRole("button", { name: "不记" }));
  fireEvent.click(screen.getByRole("button", { name: /确认写入 1 条/ }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]!.body).toEqual({ draft_id: "d-old", accepted: ["p01"], accepted_memory: ["mem#1"] });
  expect(posts[0]!.path).toBe("/api/rounds/r-seen/confirm");
  fireEvent.click(screen.getByRole("button", { name: "展开闭环区" }));
  fireEvent.click(screen.getByRole("button", { name: /这个项目走过/ }));
  fireEvent.click(screen.getByRole("button", { name: /历史修订/ }));
  await screen.findByText("记忆d-history");
  const button = screen.getByRole("button", { name: /确认写入/ });
  expect(button.hasAttribute("disabled")).toBe(true);
  fireEvent.click(button);
  expect(posts).toHaveLength(1);
});

it("App replace唯一ID含#精确跳转，不跳同文本另一条", async () => {
  render(<App />);
  await screen.findByText("记忆d-old");
  fireEvent.click(screen.getByText(/它提议的 · 1 条/));
  const drawer = screen.getByRole("dialog", { name: /草案 ·/ });
  fireEvent.click(within(drawer).getByRole("button", { name: /功能.*主流程/ }));
  await waitFor(() => expect(document.getElementById("entry-old#01")).not.toBeNull());
  expect(vi.mocked(HTMLElement.prototype.scrollIntoView).mock.instances).toContain(document.getElementById("entry-old#01"));
  expect(vi.mocked(HTMLElement.prototype.scrollIntoView).mock.instances).not.toContain(document.getElementById("entry-other#01"));
});

it("App 精确跳转遇到文档里缺失的Entry时提示不可唯一定位，不假跳", async () => {
  current = roundWith([
    { ...item, proposal_id: "p-missing", entry_id: "missing#03", before: null, text: "缺失候选" },
  ]);
  render(<App />);
  await screen.findByText("记忆d-old");
  fireEvent.click(screen.getByText(/它提议的 · 2 条/));
  const article = screen.getByText("缺失候选").closest("article");
  expect(article).not.toBeNull();
  fireEvent.click(within(article!).getByRole("button", { name: /功能.*主流程/ }));

  await screen.findByText(/无法唯一定位（missing#03/);
});

it("App 精确跳转遇到正文与偏好同ID多命中时提示不可唯一定位，不假跳", async () => {
  documentView = {
    ...documentView,
    preference_entries: [{ entry_id: "old#01", text: "同ID偏好", refs: ["E1"] }],
  };
  render(<App />);
  await screen.findByText("记忆d-old");
  fireEvent.click(screen.getByText(/它提议的 · 1 条/));
  const drawer = screen.getByRole("dialog", { name: /草案 ·/ });
  fireEvent.click(within(drawer).getByRole("button", { name: /功能.*主流程/ }));

  await screen.findByText(/无法唯一定位（old#01/);
});
