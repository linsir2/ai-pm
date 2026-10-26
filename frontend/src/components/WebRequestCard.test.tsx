// @vitest-environment jsdom
/* 一条联网申请（P3a）：谁提的、为什么、到哪一步、能不能批。
   能批不能批全部由 `webRequestAction` 判；组件只把结果画出来并递 decision。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WebRequestView } from "../api/types";
import { WebRequestCard } from "./WebRequestCard";

afterEach(cleanup);

function request(over: Partial<WebRequestView> = {}): WebRequestView {
  return {
    request_id: "w1",
    query: "RBAC 权限模型",
    why: "要查行业通行做法",
    origin: "seat",
    seat_id: "seat.permissions",
    pass_index: 1,
    state: "proposed",
    result_note: null,
    created_at: null,
    ...over,
  };
}

function draw(over: Partial<Parameters<typeof WebRequestCard>[0]> = {}) {
  const props = {
    request: request(),
    used: 0,
    limit: 3,
    available: true,
    busy: false,
    pendingDecision: null as
      | "approve"
      | "reject"
      | "execute"
      | "abandon"
      | null,
    historical: false,
    terminal: false,
    networkAccess: false,
    onDecide: vi.fn(),
    onRecover: vi.fn(),
    ...over,
  };
  render(<WebRequestCard {...props} />);
  return props;
}

describe("WebRequestCard · 内容", () => {
  it("query / why / 来源 / 席位 / 第几趟 / 状态都写出来", () => {
    draw();

    expect(screen.getByText("RBAC 权限模型")).toBeTruthy();
    expect(screen.getByText(/要查行业通行做法/)).toBeTruthy();
    expect(screen.getByText(/席位 · seat\.permissions · 第 1 趟/)).toBeTruthy();
    expect(screen.getByText("待批准")).toBeTruthy();
  });

  it("已经查完：显示结果说明，不再给决策按钮", () => {
    draw({ request: request({ state: "done", result_note: "搜到 3 条结果" }) });

    expect(screen.getByText("已查完")).toBeTruthy();
    expect(screen.getByText(/搜到 3 条结果/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "批准并查" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
  });

  it("查失败：失败原因照实显示，不给决策按钮（失败是终态）", () => {
    draw({ request: request({ state: "failed", result_note: "搜索超时" }) });

    expect(screen.getByText("查失败")).toBeTruthy();
    expect(screen.getByText(/搜索超时/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "批准并查" })).toBeNull();
  });
});

describe("WebRequestCard · 决策", () => {
  it("批准与拒绝各自把 decision 递出去", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: "批准并查" }));
    expect(props.onDecide).toHaveBeenCalledWith("approve");

    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    expect(props.onDecide).toHaveBeenCalledWith("reject");
  });

  it("名额满：批准禁用并写原因，拒绝还能点", () => {
    draw({ used: 3 });

    const approve = screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement;
    const reject = screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(reject.disabled).toBe(false);
    expect(screen.getByText(/联网名额已用完/)).toBeTruthy();

    const describedBy = approve.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy!)?.textContent).toContain("联网名额已用完");
  });

  it("批准在途：只有批准按钮显示处理中（R25）", () => {
    draw({ pendingDecision: "approve" });

    expect(
      (screen.getByRole("button", { name: "处理中…" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByRole("status").textContent).toContain("批准");
  });

  it("拒绝在途：只有拒绝按钮显示处理中，批准按钮文案不变（R25）", () => {
    draw({ pendingDecision: "reject" });

    const approve = screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement;
    const reject = screen.getByRole("button", { name: "处理中…" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(reject.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("拒绝");
  });

  it("轮忙：两个按钮都禁用并写原因", () => {
    draw({ busy: true });

    expect(
      (screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/还有动作在跑/)).toBeTruthy();
  });

  it("历史轮只读：两个按钮都禁用并写原因", () => {
    draw({ historical: true });

    expect(
      (screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/历史轮次只读/)).toBeTruthy();
  });

  it("轮已结束：两个按钮都禁用并写原因", () => {
    draw({ terminal: true });

    expect(
      (screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/这一轮已经结束/)).toBeTruthy();
  });

  it("没读到名额上限时批准禁用（不硬编码 3）", () => {
    draw({ limit: null });

    expect(
      (screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/还没读到/)).toBeTruthy();
  });

  it("服务端没有联网工具：批准禁用并说明，拒绝还能点", () => {
    draw({ available: false });

    expect(
      (screen.getByRole("button", { name: "批准并查" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(screen.getByText(/没配联网工具/)).toBeTruthy();
  });
});

describe("WebRequestCard · 超额恢复", () => {
  it("超额 approved：显示执行与放弃，点击各自递出 action", () => {
    const props = draw({
      request: request({ state: "approved" }),
      used: 4,
      limit: 3,
    });

    expect(screen.getByText(/已超联网名额（4\/3）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "执行（临时超额）" }));
    expect(props.onRecover).toHaveBeenCalledWith("execute");
    fireEvent.click(screen.getByRole("button", { name: "放弃（不计名额）" }));
    expect(props.onRecover).toHaveBeenCalledWith("abandon");
  });

  it("没有超额时 approved 不显示恢复按钮", () => {
    draw({ request: request({ state: "approved" }), used: 3, limit: 3 });

    expect(screen.queryByRole("button", { name: "执行（临时超额）" })).toBeNull();
    expect(screen.queryByRole("button", { name: "放弃（不计名额）" })).toBeNull();
  });

  it("没配联网工具：执行禁用并说明，放弃仍可点（放弃不需要联网）", () => {
    draw({
      request: request({ state: "approved" }),
      used: 4,
      limit: 3,
      available: false,
    });

    const execute = screen.getByRole("button", {
      name: "执行（临时超额）",
    }) as HTMLButtonElement;
    const abandon = screen.getByRole("button", {
      name: "放弃（不计名额）",
    }) as HTMLButtonElement;
    expect(execute.disabled).toBe(true);
    expect(abandon.disabled).toBe(false);
    expect(screen.getByText(/只能放弃/)).toBeTruthy();
  });

  it("恢复在途：两个按钮都禁用，状态行说清是哪一个动作", () => {
    draw({
      request: request({ state: "approved" }),
      used: 4,
      limit: 3,
      pendingDecision: "abandon",
    });

    expect(
      (screen.getByRole("button", { name: "处理中…" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", {
        name: "执行（临时超额）",
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("放弃");
  });
});

describe("WebRequestCard · 第三方披露", () => {
  it("工具声明外发时，在批准按钮旁说清检索词会发给第三方", () => {
    draw({ networkAccess: true });
    expect(screen.getByText(/检索词会发给第三方/)).toBeTruthy();
  });

  it("工具不声明外发时不说那句", () => {
    draw({ networkAccess: false });
    expect(screen.queryByText(/检索词会发给第三方/)).toBeNull();
  });
});
