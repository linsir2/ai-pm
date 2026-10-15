// @vitest-environment jsdom
/* 模板库数据层（`06` §3.2）：真相源从后端读回来，不在前端推算。 */

import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TemplateSectionPayload, TemplateView } from "../api/types";
import { api } from "../api/client";
import { useTemplateLibrary } from "./useTemplateLibrary";

afterEach(() => vi.restoreAllMocks());

const BUILTIN: TemplateView = {
  template_id: "standard",
  title: "标准版",
  builtin: true,
  sections: [],
};

const CUSTOM: TemplateView = {
  template_id: "tpl-1",
  title: "社区活动",
  builtin: false,
  sections: [],
};

const PAYLOAD: TemplateSectionPayload[] = [
  { id: "s5", title: "功能", purpose: "p", role: "feature", slots: [] },
];

function stubClient() {
  const listTemplates = vi.fn().mockResolvedValue([BUILTIN, CUSTOM]);
  const createTemplate = vi.fn().mockResolvedValue(CUSTOM);
  const updateTemplate = vi.fn().mockResolvedValue({ ...CUSTOM, title: "改名了" });
  const deleteTemplate = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(api, "listTemplates").mockImplementation(listTemplates);
  vi.spyOn(api, "createTemplate").mockImplementation(createTemplate);
  vi.spyOn(api, "updateTemplate").mockImplementation(updateTemplate);
  vi.spyOn(api, "deleteTemplate").mockImplementation(deleteTemplate);
  return { listTemplates, createTemplate, updateTemplate, deleteTemplate };
}

describe("useTemplateLibrary", () => {
  it("进这一屏才拉一次库（不在应用启动时打）", async () => {
    const calls = stubClient();

    const inactive = renderHook(() => useTemplateLibrary(false));
    expect(calls.listTemplates).not.toHaveBeenCalled();
    inactive.unmount();

    const active = renderHook(() => useTemplateLibrary(true));
    await waitFor(() => expect(active.result.current.items).toHaveLength(2));
    expect(calls.listTemplates).toHaveBeenCalledTimes(1);
  });

  it("新建后把后端返回的那份并进列表（不自己造一条）", async () => {
    stubClient();
    const { result } = renderHook(() => useTemplateLibrary(true));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    await act(async () => {
      await result.current.create("社区活动", PAYLOAD);
    });

    expect(result.current.items.some((item) => item.template_id === "tpl-1")).toBe(true);
  });

  it("修改后用返回的那份替换原条目", async () => {
    const calls = stubClient();
    const { result } = renderHook(() => useTemplateLibrary(true));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    await act(async () => {
      await result.current.update("tpl-1", "改名了", PAYLOAD);
    });

    expect(calls.updateTemplate).toHaveBeenCalledWith("tpl-1", "改名了", PAYLOAD);
    expect(result.current.items.find((item) => item.template_id === "tpl-1")?.title).toBe(
      "改名了",
    );
  });

  it("删掉后从列表里去掉那一条", async () => {
    stubClient();
    const { result } = renderHook(() => useTemplateLibrary(true));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    await act(async () => {
      await result.current.remove("tpl-1");
    });

    expect(result.current.items.map((item) => item.template_id)).toEqual(["standard"]);
  });

  it("后端报错就把那句中文原样上屏，不静默", async () => {
    const calls = stubClient();
    calls.deleteTemplate.mockRejectedValue(
      Object.assign(new Error("内置模板不能修改或删除：light"), {}),
    );
    const { result } = renderHook(() => useTemplateLibrary(true));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    await act(async () => {
      const done = await result.current.remove("light");
      expect(done).toBe(false);
    });

    expect(result.current.error).toBe("内置模板不能修改或删除：light");
    expect(result.current.items).toHaveLength(2);
  });
});