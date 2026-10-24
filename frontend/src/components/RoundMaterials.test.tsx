// @vitest-environment jsdom
/* 材料来源徽标（P2.8b）：手勾 > 手动带料 > 自动。 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RoundMaterialView } from "../api/types";
import { RoundMaterials } from "./RoundMaterials";

afterEach(cleanup);

const material = {
  material_id: "m1",
  title: "需求.md",
  source_ref: "attachment:需求.md",
  origin: "upload",
  parser_id: "local-text",
  chunk_count: 1,
  chunks: [],
  truncated: false,
  warnings: [],
} as RoundMaterialView;

describe("RoundMaterials 来源徽标", () => {
  it("手勾 / 自动；老轮没有来源记录就不画", () => {
    const { container, rerender } = render(
      <RoundMaterials
        materials={[material]}
        pinnedMaterials={["m1"]}
        hasRetrievals
      />,
    );
    expect(container.textContent).toContain("手勾");

    rerender(<RoundMaterials materials={[material]} hasRetrievals />);
    expect(container.textContent).toContain("自动");

    rerender(<RoundMaterials materials={[material]} />);
    expect(container.textContent).not.toContain("自动");
  });
});
