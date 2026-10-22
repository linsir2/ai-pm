"""真 PDF 语料集（批 16 · R5 的测量底座）。

用**生产路径**的 `HttpxWebFetcher`（SSRF 钉 IP / 重试 / 5MB 上限 / 类型护栏）把 PDF
抓下来存盘——与 `page_set.py`（网页语料）同一套做法。

为什么要专门抓 PDF：R5 的父块只做 PDF，而**父块怎么分**（页 / 页内累积到 N 字）
必须用**真 PDF 的真实版面**来量——页数、每页段数、段长分布都影响答案。

    cd backend && uv run python -u scripts/pdf_set.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

OUT = Path("../.codex/runs/2026-10-21/pdf_set")

PDFS = [
    {"name": "arxiv_attention", "url": "https://arxiv.org/pdf/1706.03762"},
    {"name": "w3c_html52", "url": "https://www.w3.org/TR/2019/REC-html52-20191209/single-page.html"},
    {"name": "mdn_http", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview"},
    {"name": "python_tutorial", "url": "https://docs.python.org/3/tutorial/introduction.html"},
]


async def _fetch_all() -> list[dict]:
    from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher

    OUT.mkdir(parents=True, exist_ok=True)
    fetcher = HttpxWebFetcher()
    saved: list[dict] = []
    for item in PDFS:
        path = OUT / f"{item['name']}.pdf"
        try:
            fetched = await fetcher.fetch(url=item["url"])
            data, media_type, final_url = (
                fetched.content, fetched.media_type, fetched.final_url)
        except Exception as error:  # noqa: BLE001 —— 抓不到就记下来，不中断
            print(f"  ✗ {item['name']}: {type(error).__name__} {error}")
            saved.append({**item, "status": "fetch-failed", "error": str(error)[:200]})
            continue
        if not data.startswith(b"%PDF"):
            print(f"  ✗ {item['name']}: 不是 PDF（{media_type}）")
            saved.append({**item, "status": "not-pdf", "media_type": media_type})
            continue
        path.write_bytes(data)
        print(f"  ✔ {item['name']:18} {len(data):>9} 字节 {media_type}")
        saved.append({**item, "status": "ok", "final_url": final_url,
                      "media_type": media_type, "bytes": len(data),
                      "pdf": str(path)})
    return saved


def main() -> int:
    saved = asyncio.run(_fetch_all())
    (OUT / "index.json").write_text(
        json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for item in saved if item.get("status") == "ok")
    print(f"抓到 {ok}/{len(PDFS)} 个真 PDF")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
