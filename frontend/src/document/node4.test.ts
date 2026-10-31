import { describe, expect, it } from "vitest";
import { sectionOfEntry } from "./structure";
import { sectionStatus } from "./status";
import { defaultAccepted } from "../api/payload";
import type { DocumentView, DraftView } from "../api/types";

describe("node4 contracts", () => {
  const section = { section_id: "s5", blocks: [
    { entries: [], children: [{ entries: [{ entry_id: "r#1", text: "flow" }], children: [] }] },
  ] };
  it("done历史草案/题不算pending，嵌套功能字段仍算filled", () => {
    expect(sectionStatus(section, { waiting_on: "done", draft: { proposals: [
      { target_label: "s5/order.main_flow" },
    ] }, questions: [{ kind: "conflict", target_label: "s5" }] })).toBe("filled");
    expect(sectionStatus(section, { waiting_on: "draft_card", draft: { proposals: [
      { target_label: "s5/order.main_flow" },
    ] } })).toBe("gap");
    expect(sectionStatus(section, { waiting_on: "questionnaire", questions: [
      { kind: "conflict", target_label: "s5" },
    ] })).toBe("conflict");
  });
  it("多命中同Entry（同块/跨正文偏好）不能假跳，含#唯一ID可定位", () => {
    const doc = { sections: [section], preference_entries: [] } as unknown as DocumentView;
    expect(sectionOfEntry(doc, "r#1")).toBe("s5");
    expect(sectionOfEntry(doc, "missing")).toBeNull();
    expect(sectionOfEntry({ ...doc, preference_entries: [
      { entry_id: "r#1", text: "other", refs: [] },
    ] }, "r#1")).toBeNull();
    const twice = { section_id: "s5", blocks: [{ entries: [
      { entry_id: "r#1" }, { entry_id: "r#1" },
    ], children: [] }] };
    expect(sectionOfEntry({ ...doc, sections: [twice] } as unknown as DocumentView, "r#1")).toBeNull();
  });
  it("默认勾选同时守来源/simulation/overlap/机械过目，不由单一flag接管", () => {
    const draft = { proposals: [
      { proposal_id: "safe", origin_type: "user", requires_review: false },
      { proposal_id: "proposed", origin_type: "proposed", requires_review: false },
      { proposal_id: "simulation", origin_type: "derived", simulation_based: true, requires_review: false },
      { proposal_id: "overlap", origin_type: "user", overlap: "1/1", requires_review: false },
      { proposal_id: "review", origin_type: "user", requires_review: true },
    ] } as unknown as DraftView;
    expect(defaultAccepted(draft)).toEqual(["safe"]);
  });
});
