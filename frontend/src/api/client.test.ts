import { describe, expect, it } from "vitest";

import { apiErrorFrom } from "./client";

describe("apiErrorFrom（P29 眼睛检查 F10）", () => {
  it("后端自己的错误（JSON 里有 code/message）照原样用", () => {
    const error = apiErrorFrom(
      409,
      { code: "material_frozen", message: "这份材料已经发过编号 E7，不能删", details: { eids: ["E7"] } },
      "请求失败",
    );

    expect(error.code).toBe("material_frozen");
    expect(error.message).toContain("E7");
    expect(error.details).toEqual({ eids: ["E7"] });
  });

  it("**不是 JSON** 的回包（后端没起来 / 正在重启 / 代理回的 500）要说人话", () => {
    const error = apiErrorFrom(500, {}, "请求失败");

    expect(error.code).toBe("backend_unreachable");
    expect(error.message).toContain("后端没有正常回话");
    expect(error.message).toContain("500");
  });

  it("有 code 但没有 message 的：**保住原码**、把话补成人话（别抛 'undefined'）", () => {
    const error = apiErrorFrom(503, { code: "weird" }, "请求失败");

    expect(error.code).toBe("weird");            // 原码比 http_error 更不丢信息
    expect(error.message).toBe("请求失败（HTTP 503）");
  });
});
