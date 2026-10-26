// @vitest-environment jsdom
/* 复述卡上的"联网检索词"那一栏（工具层 C-2b）：
   默认不勾；勾一下把 query 递出去；服务端没配工具时不给复选框（但要说一句实话）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundView, WebRequestView } from "../api/types";
import { OpeningCard } from "./OpeningCard";

afterEach(cleanup);

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "想做个订单导出",
    step: "claims_ready",
    waiting_on: "opening_card",
    seat_round_count: 0,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [
      { query: "RBAC 权限模型", why: "要查行业通行做法", chosen: false },
    ],
    web_requests: [],
    allow_web: false,
    web_search_available: true,
    web_search_network_access: false,
    ...over,
  } as unknown as RoundView;
}

function webRequest(over: Partial<WebRequestView> = {}): WebRequestView {
  return {
    request_id: "w1",
    query: "RBAC 权限模型",
    why: "要查行业通行做法",
    origin: "analyzer",
    seat_id: null,
    pass_index: null,
    state: "proposed",
    result_note: null,
    created_at: null,
    ...over,
  };
}

function draw(over: Partial<Parameters<typeof OpeningCard>[0]> = {}) {
  const props = {
    round: round(),
    doc: null,
    edits: {},
    seats: [],
    webQueries: [] as string[],
    approvedRequestIds: [] as string[],
    allowWeb: false,
    limit: 3 as number | null,
    pending: false,
    onChangeClaim: vi.fn(),
    onToggleSeat: vi.fn(),
    onToggleWebQuery: vi.fn(),
    onToggleWebRequest: vi.fn(),
    onToggleAllowWeb: vi.fn(),
    onSubmit: vi.fn(),
    ...over,
  };
  render(<OpeningCard {...props} />);
  return props;
}

describe("OpeningCard · 联网检索词", () => {
  it("默认不勾（联网要显式授权），但把「为什么查」写出来", () => {
    draw();

    const box = screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(screen.getByText(/要查行业通行做法/)).toBeTruthy();
  });

  it("勾一下把这条检索词递出去", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("checkbox", { name: /RBAC 权限模型/ }));

    expect(props.onToggleWebQuery).toHaveBeenCalledWith("RBAC 权限模型");
  });

  it("已经勾上的读回来是勾着的", () => {
    draw({ webQueries: ["RBAC 权限模型"] });

    expect((screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("服务端没配联网工具：不给复选框，但说清「建议了、办不了」", () => {
    draw({ round: round({ web_search_available: false }) });

    expect(screen.queryByRole("checkbox", { name: /RBAC 权限模型/ })).toBeNull();
    expect(screen.getByText(/没配联网工具/)).toBeTruthy();
  });

  it("没有联网提案：列表不出现，但 allow_web 仍可见（R22）", () => {
    draw({ round: round({ web_queries: [], web_requests: [] }) });

    expect(screen.queryByText(/分析席位建议查/)).toBeNull();
    expect(screen.queryByText(/分析席位提了/)).toBeNull();
    expect(screen.getByRole("checkbox", { name: /自动批准/ })).toBeTruthy();
  });

  it("联网工具声明会外发时，授权处要说清检索词发给第三方", () => {
    draw({
      round: round({
        web_search_available: true,
        web_search_network_access: true,
      }),
    });

    expect(screen.getByText(/检索词会发给第三方/)).toBeTruthy();
  });

  it("不外发的工具不许出现那句话", () => {
    draw({
      round: round({
        web_search_available: true,
        web_search_network_access: false,
      }),
    });

    expect(screen.queryByText(/检索词会发给第三方/)).toBeNull();
  });
});

describe("OpeningCard · 结构化联网申请（P3a）", () => {
  it("有 analyzer 申请时只渲染新卡，不再重复渲染旧 web_queries", () => {
    draw({
      round: round({
        web_queries: [{ query: "旧检索词", why: "旧理由", chosen: false }],
        web_requests: [webRequest()],
      }),
    });

    expect(screen.getByRole("checkbox", { name: /RBAC 权限模型/ })).toBeTruthy();
    expect(screen.queryByText("旧检索词")).toBeNull();
  });

  it("analyzer proposed 默认不勾；勾一下把 request_id 递出去", () => {
    const props = draw({ round: round({ web_requests: [webRequest()] }) });

    const box = screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);

    fireEvent.click(box);
    expect(props.onToggleWebRequest).toHaveBeenCalledWith("w1");
  });

  it("已勾的从草稿读回来；服务端已批准的显示为勾着且不可改", () => {
    draw({
      round: round({ web_requests: [webRequest()] }),
      approvedRequestIds: ["w1"],
    });
    expect(
      (screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement).checked,
    ).toBe(true);

    cleanup();
    draw({
      round: round({ web_requests: [webRequest({ state: "approved" })] }),
    });
    const approved = screen.getByRole("checkbox", {
      name: /RBAC 权限模型/,
    }) as HTMLInputElement;
    expect(approved.checked).toBe(true);
    expect(approved.disabled).toBe(true);
  });

  it("allow_web 默认关；打开时把 true 递出去", () => {
    const props = draw({ round: round({ web_requests: [webRequest()] }) });

    const box = screen.getByRole("checkbox", { name: /自动批准/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(props.onToggleAllowWeb).toHaveBeenCalledWith(true);
  });

  it("allow_web 旁披露第三方外发；工具不声明外发就不说", () => {
    draw({
      round: round({
        web_requests: [webRequest()],
        web_search_network_access: true,
      }),
    });
    expect(screen.getByText(/检索词会发给第三方/)).toBeTruthy();

    cleanup();
    draw({
      round: round({
        web_requests: [webRequest()],
        web_search_network_access: false,
      }),
    });
    expect(screen.queryByText(/检索词会发给第三方/)).toBeNull();
  });

  it("没配联网工具：申请只说明办不了，不给复选框，allow_web 也禁用", () => {
    draw({
      round: round({
        web_requests: [webRequest()],
        web_search_available: false,
      }),
    });

    expect(screen.queryByRole("checkbox", { name: /RBAC 权限模型/ })).toBeNull();
    expect(screen.getByText(/没配联网工具/)).toBeTruthy();
    expect(
      (screen.getByRole("checkbox", { name: /自动批准/ }) as HTMLInputElement).disabled,
    ).toBe(true);
  });

  it("没有提案也没有工具：allow_web 仍渲染，但禁用并说明（R22）", () => {
    draw({
      round: round({
        web_queries: [],
        web_requests: [],
        web_search_available: false,
      }),
    });

    const allow = screen.getByRole("checkbox", { name: /自动批准/ }) as HTMLInputElement;
    expect(allow.disabled).toBe(true);
    expect(screen.getByText(/没配联网工具/)).toBeTruthy();
  });

  it("limit=2、已勾 2 条：第 3 条禁用，并显示剩余 0/2（R23）", () => {
    draw({
      round: round({
        web_requests: [
          webRequest({ request_id: "w1", query: "第一条" }),
          webRequest({ request_id: "w2", query: "第二条" }),
          webRequest({ request_id: "w3", query: "第三条" }),
        ],
      }),
      approvedRequestIds: ["w1", "w2"],
      limit: 2,
    });

    const third = screen.getByRole("checkbox", { name: /第三条/ }) as HTMLInputElement;
    expect(third.disabled).toBe(true);
    expect(third.checked).toBe(false);
    expect(screen.getByText(/剩余 0\/2/)).toBeTruthy();

    const id = third.getAttribute("aria-describedby");
    expect(id).not.toBeNull();
    expect(document.getElementById(id!)?.textContent).toContain("名额已用完");
  });

  it("已有一条 approved：剩余 2/3，未勾项仍可勾（R23）", () => {
    draw({
      round: round({
        web_requests: [
          webRequest({ request_id: "w1", state: "approved" }),
          webRequest({ request_id: "w2", query: "第二条" }),
        ],
      }),
      limit: 3,
    });

    expect(screen.getByText(/剩余 2\/3/)).toBeTruthy();
    const second = screen.getByRole("checkbox", { name: /第二条/ }) as HTMLInputElement;
    expect(second.disabled).toBe(false);
  });

  it("limit 没读到：未勾项禁用并说明（R23）", () => {
    draw({
      round: round({ web_requests: [webRequest()] }),
      limit: null,
    });

    const box = screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(screen.getByText(/还没读到/)).toBeTruthy();
  });

  it("勾选框的可访问名保留可见的「为什么查」文字", () => {
    draw({ round: round({ web_requests: [webRequest()] }) });

    expect(
      screen.getByRole("checkbox", {
        name: /RBAC 权限模型.*要查行业通行做法/,
      }),
    ).toBeTruthy();
  });
});


it("显示分析者这一趟读了什么——批准主张之前看得见依据", () => {
  draw({ round: round({ analysis_basis: "读了 s5 与 s11 的内容" }) });
  expect(screen.getByText(/它这一趟读了/)).toBeTruthy();
  expect(screen.getByText(/s5 与 s11/)).toBeTruthy();
});

it("没有 analysis_basis 时不渲染那一行（老行回落空串）", () => {
  draw();
  expect(screen.queryByText(/它这一趟读了/)).toBeNull();
});

describe("OpeningCard · 目录里自己加的席位要能取消（2026-10-14 修复）", () => {
  const extra = {
    seat_id: "seat.permissions",
    title: "权限与角色",
    focus: "谁看得见、谁改得动",
    always_on: false,
    matched_keywords: [] as string[],
    why: "",
  };

  it("还没加：按钮是「+ 加上」，点了把席位递出去", () => {
    const props = draw({
      round: round({ available_seats: [extra] as never }),
      seats: [],
    });

    fireEvent.click(screen.getByRole("button", { name: "+ 加上" }));
    expect(props.onToggleSeat).toHaveBeenCalledWith("seat.permissions");
  });

  it("加上了：按钮变「已加上 · 取消」且**点得动**，再点一次就移除（防加得上删不掉）", () => {
    const props = draw({
      round: round({ available_seats: [extra] as never }),
      seats: ["seat.permissions"],
    });

    const button = screen.getByRole("button", { name: "已加上 · 取消" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(props.onToggleSeat).toHaveBeenCalledWith("seat.permissions");
  });
});
