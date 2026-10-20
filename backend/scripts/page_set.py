#!/usr/bin/env python3
"""批 6c · **真实网站**页面集：抓下来存盘 + 量四种清洗口径。

**这个脚本就是计划里说的"我们自己的页面集基准"**（之前缺的就是它：
`.codex/runs/` 一份真实网页 HTML 都没有）。它做两件事：

1. **抓页面存盘**：用**生产路径**的 `HttpxWebFetcher`（带 SSRF 钉 IP / 重试 /
   5MB / 3 跳 / Content-Type 护栏），把原始 HTML 存到 `.codex/runs/2026-10-19/pages/`——
   之后所有实验都跑这批**固定的**页面，数字才可复现。
2. **量四种口径**：现状（bs4 + CHROME_TAGS 拍平）/ trafilatura standard /
   favor_recall / fast。每页报：正文字数、**保留了哪些标志性句子**（人工标注的
   "这段必须在"）、**丢了哪些**。

**为什么标注"必须在/可丢"而不是算 F1**：WCXB 那种逐词 F1 需要标注全篇，
我们这个规模做不起，也不需要——我们要回答的是产品问题：
「主内容抽取会不会**丢我们真正要的那段**」。那是**判据**，不是相似度。

**跑法**：
    cd backend && set -a && . ./.env && set +a && \\
      python3 scripts/page_set.py            # 抓 + 存 + 量
    cd backend && python3 scripts/page_set.py --measure   # 只量（不重抓）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

OUT = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "pages"

#: 真实页面集：**不同页面类型**（业界 WCXB 的分类：文章/文档/产品/论坛/服务页）。
#: 全是**中文**页面——我们这个系统的实际语料就是中文。
#: 每条带 `must_keep`（这段**必须在**：丢了就是丢内容）与 `may_drop`（这段是站点噪音）。
PAGES = [
    {
        "name": "ithome",
        "url": "https://www.ithome.com/",
        "type": "科技门户/首页",
    },
    {
        "name": "36kr",
        "url": "https://www.36kr.com/",
        "type": "创投门户/首页",
    },
    {
        "name": "juejin_collections",
        "url": "https://juejin.cn/collections",
        "type": "技术站/列表页",
    },
    {
        "name": "cnblogs",
        "url": "https://www.cnblogs.com/",
        "type": "技术博客/首页",
    },
    {
        "name": "runoob",
        "url": "https://www.runoob.com/js/js-tutorial.html",
        "type": "教程站/文章页",
    },
    {
        "name": "github_cpython",
        "url": "https://github.com/python/cpython",
        "type": "代码托管/首页（噪音极重）",
    },
    {
        "name": "fastapi_zh",
        "url": "https://fastapi.tiangolo.com/zh/tutorial/",
        "type": "文档站",
    },
    {
        "name": "vue_zh",
        "url": "https://cn.vuejs.org/guide/introduction.html",
        "type": "文档站",
    },
    {
        "name": "react_zh",
        "url": "https://zh-hans.react.dev/learn",
        "type": "文档站",
    },
    {
        "name": "mdn_zh",
        "url": "https://developer.mozilla.org/zh-CN/docs/Web/HTTP",
        "type": "文档站",
    },
    {
        "name": "mdn_css_zh",
        "url": "https://developer.mozilla.org/zh-CN/docs/Web/CSS",
        "type": "文档站（重参考表）",
    },
    {
        "name": "spring_boot_ref",
        "url": "https://docs.spring.io/spring-boot/docs/current/reference/index.html",
        "type": "文档站（重表格/代码）",
    },
    {
        "name": "people_news",
        "url": "http://www.people.com.cn/",
        "type": "门户/首页（链接多）",
    },
    {
        "name": "zhihu_home",
        "url": "https://www.zhihu.com/",
        "type": "门户/首页（链接多）",
    },
    {
        "name": "bing_home",
        "url": "https://cn.bing.com/",
        "type": "搜索首页（几乎全是链接）",
    },
    {
        "name": "infoq_home",
        "url": "https://www.infoq.cn/",
        "type": "技术门户/首页",
    },
    {
        "name": "segmentfault_home",
        "url": "https://segmentfault.com/",
        "type": "技术门户/首页",
    },
]


def _fetch_all() -> list[dict]:
    """用生产路径的抓取器把页面抓下来存盘。"""

    from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher

    OUT.mkdir(parents=True, exist_ok=True)
    fetcher = HttpxWebFetcher()
    saved: list[dict] = []
    for page in PAGES:
        html_path = OUT / f"{page['name']}.html"
        try:
            import asyncio

            fetched = asyncio.run(fetcher.fetch(url=page["url"]))
            page_bytes, media_type, final_url = (
                fetched.content, fetched.media_type, fetched.final_url)
        except Exception as error:  # noqa: BLE001 —— 抓不到就记下来，不中断
            print(f"  ✗ {page['name']}: {type(error).__name__} {error}")
            saved.append({**page, "status": "fetch-failed", "error": str(error)[:200]})
            continue
        html_path.write_bytes(page_bytes)
        (OUT / f"{page['name']}.json").write_text(
            json.dumps({**page, "final_url": final_url, "media_type": media_type,
                        "bytes": len(page_bytes)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"  ✔ {page['name']:18} {len(page_bytes):>8} 字节  {media_type}")
        saved.append({**page, "status": "ok", "final_url": final_url,
                      "media_type": media_type, "bytes": len(page_bytes),
                      "html": str(html_path)})
    return saved


def _current_text(html: bytes) -> str:
    """现状口径：bs4 + CHROME_TAGS 拍平（生产代码同一条）。"""

    from pmstudio.infrastructure.tools.extract import html_text

    return html_text(html)[0]


def _traf_text(html: bytes, **kwargs) -> str | None:  # type: ignore[no-untyped-def]
    import trafilatura

    return trafilatura.extract(html.decode("utf-8", "replace"), **kwargs)


def measure(pages: list[dict]) -> None:
    """逐行对比：现状 vs trafilatura。**不靠我手挑词**（手挑过一次，标注词根本
    不在原文里，差点误判成"抽取器丢内容"）。改成客观的行级 diff：

    - **丢的行**（现状有、抽取后没有）→ 要看的是：它是正文还是站点噪音？
    - **多的行**（抽取后有、现状没有）→ 要看的是：它是正文（好）还是噪音（坏）？
    """

    import trafilatura

    print(f"\n{'页面':20} {'现状':>7} {'traf':>7} {'丢行':>5} {'多行':>5}  "
          f"{'丢的是正文?':>10}")
    print("-" * 80)
    totals = {"cur": 0, "traf": 0, "dropped": 0, "added": 0, "pages": 0}
    for page in pages:
        if page.get("status") != "ok":
            continue
        html = (OUT / f"{page['name']}.html").read_bytes()
        current = _current_text(html)
        std = _traf_text(html) or ""
        cur_lines = {line.strip() for line in current.splitlines() if line.strip()}
        traf_lines = {line.strip() for line in std.splitlines() if line.strip()}
        dropped = cur_lines - traf_lines
        added = traf_lines - cur_lines
        # 粗判"丢的是不是正文"：站点噪音词表里的行不算正文
        noise_words = ("版权所有", "网站地图", "联系我们", "上一篇", "下一篇", "Previous",
                       "Next", "Navigation", "登录", "注册", "版权", "备案",
                       "责任编辑", "分享到", "广告")
        dropped_content = [d for d in dropped
                           if not any(w in d for w in noise_words) and len(d) > 12]
        print(f"{page['name']:20} {len(current):>7} {len(std):>7} {len(dropped):>5}"
              f" {len(added):>5}  {len(dropped_content):>10}")
        totals["cur"] += len(current)
        totals["traf"] += len(std)
        totals["dropped"] += len(dropped)
        totals["added"] += len(added)
        totals["pages"] += 1
    print("-" * 80)
    print(f"合计 {totals['pages']} 页：现状 {totals['cur']} 字 → trafilatura "
          f"{totals['traf']} 字（{totals['traf'] / max(1, totals['cur']):.0%}）；"
          f"逐行：丢 {totals['dropped']} 行、留 {totals['added']} 行")
    print(f"trafilatura {trafilatura.__version__}"
          "（Apache-2.0；运行时依赖 +charset_normalizer +courlan +htmldate +justext，"
          "lxml 需 ≥6.1.3）")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if "--measure" in sys.argv:
        pages = [json.loads(p.read_text(encoding="utf-8"))
                 for p in sorted(OUT.glob("*.json"))]
    else:
        print("抓页面（走生产抓取器）…")
        pages = _fetch_all()
    measure(pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
