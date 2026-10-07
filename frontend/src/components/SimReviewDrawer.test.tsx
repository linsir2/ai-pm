// @vitest-environment jsdom
/* 模拟评测抽屉（M11-T9）：两段式——没跑过是选择器（默认勾选、上限、开始按钮），
   跑过是报告（三档计数 + advisories + 逐 persona/功能展开 + 常驻声明）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DocumentView,
  PersonaSchemaView,
  SimReviewView,
} from "../api/types";
import { SimReviewDrawer } from "./SimReviewDrawer";

afterEach(cleanup);

const personas: PersonaSchemaView[] = [
  { persona_id: "persona.first_touch", title: "首次上手者", situation: "第一次用" },
  { persona_id: "persona.bulk_operator", title: "高频批量操作者", situation: "每天重复" },
  { persona_id: "persona.interrupted", title: "中途离开者", situation: "做到一半离开" },
  { persona_id: "persona.edge_maker", title: "异常状态制造者", situation: "故意走边界" },
];

function block(feature_slug: string | null, children: unknown[] = []) {
  return {
    block_id: `b-${feature_slug ?? "s"}`,
    kind: feature_slug ? "feature" : "section",
    entries: [],
    children,
    field: null,
    field_title: null,
    feature_slug,
    manually_edited: false,
    multiple: false,
  };
}

const document_ = {
  document_id: "d1",
  project_id: "p1",
  title: "订单中台",
  version_no: 2,
  sections: [
    { section_id: "s5", title: "功能需求", blocks: [block("order-export"), block("checkout")] },
  ],
  preference_entries: [],
} as unknown as DocumentView;

const review: SimReviewView = {
  runs: 1,
  max_runs: 1,
  evaluated_version: 2,
  summary: { smooth: 0, friction: 1, blocked: 1 },
  advisories: ["功能 order-export：全体模拟使用者未能走通——建议人工复核"],
  personas: [
    {
      persona_id: "persona.first_touch",
      title: "首次上手者",
      features: [
        {
          feature_slug: "order-export",
          started: true,
          verdict: "blocked",
          verdict_because: ["F1"],
          walkthrough: [
            {
              step_index: 1,
              step_text: "进入订单列表",
              from_doc: "en-flow",
              action: "打开订单列表页",
              outcome: "stuck",
              need: "文档没写导出按钮在哪",
            },
          ],
          frictions: [
            {
              local_id: "F1",
              kind: "dead_end",
              entry_id: "en-flow",
              field: "",
              quote: "",
              not_documented: true,
              blocks_flow: true,
              note: "点了导出没有反应",
            },
          ],
          acceptance_checks: [
            { local_id: "A1", entry_id: "en-acc1", status: "unverified", note: "" },
          ],
        },
      ],
    },
    {
      persona_id: "persona.edge_maker",
      title: "异常状态制造者",
      features: [
        {
          feature_slug: "order-export",
          started: true,
          verdict: "friction",
          verdict_because: ["F1"],
          walkthrough: [],
          frictions: [],
          acceptance_checks: [],
        },
      ],
    },
  ],
};

function draw(over: Partial<Parameters<typeof SimReviewDrawer>[0]> = {}) {
  const props = {
    review: null as SimReviewView | null,
    personas,
    document: document_,
    limits: { personas: 3, features: 2 },
    pending: false,
    onRun: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  render(<SimReviewDrawer {...props} />);
  return props;
}

describe("SimReviewDrawer · 选择器", () => {
  it("默认勾前 3 个 persona；功能按文档序展示", () => {
    draw();

    const inputs = screen.getAllByRole("checkbox") as HTMLInputElement[];
    const checked = inputs.filter((item) => item.checked).map((item) => item.name);
    expect(checked).toEqual([
      "persona.first_touch",
      "persona.bulk_operator",
      "persona.interrupted",
      "order-export",
      "checkout",
    ]);
    expect(screen.getByText("首次上手者")).toBeTruthy();
    expect(screen.getByText("异常状态制造者")).toBeTruthy();
  });

  it("开始按钮把勾选交给 onRun", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: /开始评测/ }));

    expect(props.onRun).toHaveBeenCalledWith(
      ["persona.first_touch", "persona.bulk_operator", "persona.interrupted"],
      ["order-export", "checkout"],
    );
  });

  it("功能全不勾时按钮禁用（0 个没法评）", () => {
    draw();

    fireEvent.click(screen.getByRole("checkbox", { name: /order-export/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /checkout/ }));

    const button = screen.getByRole("button", { name: /开始评测/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("运行中显示等待文案，不给开始按钮", () => {
    draw({ pending: true });

    expect(screen.getByText(/正在走查/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /开始评测/ })).toBeNull();
  });
});

describe("SimReviewDrawer · 报告", () => {
  it("三档计数、基于版本、常驻声明、advisories 都在", () => {
    draw({ review });

    expect(screen.getByText(/顺 0 \/ 有摩擦 1 \/ 走不通 1/)).toBeTruthy();
    expect(screen.getByText(/基于文档 v2 的走查/)).toBeTruthy();
    expect(screen.getByText(/不是真实用户数据/)).toBeTruthy();
    expect(screen.getByText(/建议人工复核/)).toBeTruthy();
  });

  it("逐 persona 展开里能看到走查步、摩擦点与验收核对", () => {
    draw({ review });

    fireEvent.click(screen.getByText(/首次上手者/));

    expect(screen.getByText(/文档没写导出按钮在哪/)).toBeTruthy();
    expect(screen.getByText(/点了导出没有反应/)).toBeTruthy();
    expect(screen.getByText(/en-acc1/)).toBeTruthy();
    expect(screen.getAllByText(/走不通/).length).toBeGreaterThan(0);
  });
});
