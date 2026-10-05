import { afterEach, describe, expect, it } from "vitest";

import { api, apiErrorFrom } from "./client";

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


/* ── request() 的 fetch 路径（M7 ⑥）─────────────────────────────────────────

这一段之前**不存在**：32 行测试只覆盖了纯函数 `apiErrorFrom`，而真正发请求的
`request()` 零测试。后果就是下面这个 bug 能一直活着：

    routes.py:398  DELETE .../memory/{id}  →  status_code=204
    routes.py:408  return Response(status_code=204)     ← 无 body
    client.ts:97    return (await response.json())      ← 空 body 抛 SyntaxError

于是「忘掉一条记忆」每次都失败：红条一条，而且 `useWorkspaceData.ts:300`
之后的 `refreshDocument` **永不执行**——后端已经删了，抽屉里那条还在，
下一轮提示词也仍然带着它。

所以这里用 `globalThis.fetch` 打桩，**经公开 API 测**（不改 `request` 的导出）。
*/

type FetchStub = Partial<Response>;

/** 造一个 `fetch` 替身。默认行为按"有 JSON body"给，各用例只覆盖自己关心的字段。 */
function stubFetch(overrides: FetchStub = {}): void {
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => ({}),
    text: async () => "{}",
    ...overrides,
  })) as unknown as typeof fetch;
}

/** 204 的真实行为：body 为空，`json()` 抛 `SyntaxError: Unexpected end of JSON input`。 */
function stubNoContent(): void {
  stubFetch({
    status: 204,
    statusText: "No Content",
    json: async () => {
      throw new SyntaxError("Unexpected end of JSON input");
    },
    text: async () => "",
  });
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("request() 遇无 body 的成功回包", () => {
  it("204（删记忆那个端点）**不许抛**——它本来就该返回 undefined", async () => {
    stubNoContent();

    await expect(api.forgetMemory("p1", "m1")).resolves.toBeUndefined();
  });

  it("205 与空字符串 body 同理（不只认 204 这一种）", async () => {
    stubNoContent();
    await expect(api.forgetMemory("p1", "m1")).resolves.toBeUndefined();

    stubFetch({ text: async () => "", json: async () => ({}) });
    await expect(api.forgetMemory("p1", "m1")).resolves.toBeUndefined();
  });

  it("正常 JSON 回包照原样解析（别把这条一起改坏）", async () => {
    stubFetch({
      text: async () => JSON.stringify([{ material_id: "m1" }]),
      json: async () => [{ material_id: "m1" }],
    });

    const list = await api.deleteMaterial("p1", "m1");

    expect(list).toEqual([{ material_id: "m1" }]);
  });

  it("body 声称有内容却不是合法 JSON 时**仍然要抛**——别把错误一起吞掉", async () => {
    stubFetch({
      text: async () => "{ 这不是 JSON",
      json: async () => JSON.parse("{ 这不是 JSON"),
    });

    await expect(api.deleteMaterial("p1", "m1")).rejects.toBeTruthy();
  });
});

describe("request() 非 2xx 的回包", () => {
  it("后端自己的错误照原样抛成 ApiError（带 code 与 message）", async () => {
    stubFetch({
      ok: false,
      status: 409,
      text: async () => JSON.stringify({ code: "material_frozen", message: "这份材料发过编号" }),
      json: async () => ({ code: "material_frozen", message: "这份材料发过编号" }),
    });

    await expect(api.deleteMaterial("p1", "m1")).rejects.toMatchObject({
      code: "material_frozen",
      message: "这份材料发过编号",
    });
  });
});
