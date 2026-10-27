"""真 Tavily 三条入库口径的对照实验（冻结语料，可复跑）。

口径：
  A content(现状)  —— Tavily 按 query 挑的 3 段片段
  B raw_content    —— 整页 markdown，一字不改
  C raw_content-净 —— 整页 markdown，剔掉「整行只有一个 markdown 链接/图片」的导航行

判据（固定语料 + 真检索 + 真模型）：
  · 目标词在本路语料里的存在率  → 内容完整性
  · 目标材料进 top5 的比例       → 召回
  · 目标名次                     → 排序
  · 块数 / 向量耗时              → 成本
"""
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")
import httpx
import jieba

QUERIES = [
    "向量检索 企业知识库 召回效果",
    "RAG 知识库 切片 策略 实践",
    "大模型 推理 成本 优化 部署",
]
N_RESULTS = 5
OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
LINK_LINE = re.compile(r"^!?\[[^\]]*\]\([^)]*\)$")
#: 单块上限（字）——与生产口径 `WEB_CHUNK_CHARS` 一致

def env(key):
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            if k == key:
                return v

def search(key, query):
    payload = {"api_key": key, "query": query, "max_results": N_RESULTS,
               "search_depth": "basic", "include_raw_content": "markdown"}
    r = httpx.post("https://api.tavily.com/search", json=payload, timeout=120)
    r.raise_for_status()
    return r.json().get("results", [])

def tokens(text):
    words = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        for w in jieba.cut(line):
            w = w.strip()
            if len(w) >= 2 and re.match(r"^[\w一-鿿]+$", w):
                words.append(w)
    return words

def strip_nav(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and LINK_LINE.fullmatch(s):
            continue
        out.append(line)
    return "\n".join(out)

def main():
    key = env("PMSTUDIO_TAVILY_API_KEY")
    OUT.mkdir(parents=True, exist_ok=True)
    corpus = {
        "A content(现状)": [],
        "B raw_content": [],
        "C raw_content-净": [],
    }
    queries = []
    for q in QUERIES:
        for item in search(key, q):
            url = item.get("url", "")
            title = item.get("title", "")
            content = item.get("content") or ""
            raw = item.get("raw_content") or content
            corpus["A content(现状)"].append(
                {"q": q, "url": url, "title": title, "text": content})
            corpus["B raw_content"].append(
                {"q": q, "url": url, "title": title, "text": raw})
            corpus["C raw_content-净"].append(
                {"q": q, "url": url, "title": title, "text": strip_nav(raw)})
            # 查询词取自**页面真实内容**（raw_content 的中段），每条 3 个
            ws = tokens(raw)
            mid = len(ws) // 2
            for w in ws[mid:mid + 3]:
                queries.append((w, q, url))
    (OUT / "frozen_queries.json").write_text(
        json.dumps(
            {"queries": QUERIES, "items": queries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    for label, items in corpus.items():
        (OUT / f"{label}.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"语料：{len(corpus['A content(现状)'])} 份材料 · 查询词 {len(queries)} 条")
    _retrieve(corpus, queries)

def _retrieve(corpus, queries):

    from pmstudio.application.dto import MaterialChunk, ParsedMaterial
    from pmstudio.bootstrap.container import UuidIdGenerator
    from pmstudio.infrastructure.sqlite.database import connect
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
    from pmstudio.infrastructure.sqlite.materials import (
        SqliteFtsRetriever,
        SqliteHybridRetriever,
        SqliteMaterialStore,
    )
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from pmstudio.infrastructure.tools.extract import chunk_text
    from pmstudio.infrastructure.tools.webclean import normalize_web_text

    embedder = FastEmbedEmbedder()
    PROJECT, OWNER = "p1", "o1"
    print("\n建库（生产路径：真材料仓 + 真向量索引）…")
    for label, items in corpus.items():
        db = OUT / f"{label.replace('/', '_')}.db"
        if db.exists():
            db.unlink()
        conn = connect(db, embedder=embedder)
        store = SqliteMaterialStore(conn, ids=UuidIdGenerator())
        vi = SqliteVectorIndex(conn, embedder=embedder)
        nblocks = 0
        url2mid = {}
        for item in items:
            clean = normalize_web_text(item["text"])
            chunks = chunk_text(clean, parser_id="tavily", max_chars=400)
            nblocks += len(chunks)
            parsed = ParsedMaterial(
                origin="web_search", title=item["title"], source_ref=item["url"],
                media_type="text/html", bytes_len=len(item["text"].encode()),
                chunks=tuple(MaterialChunk(chunk_id=f"c{j:02d}", ordinal=j, text=ch.text,
                                           heading_path=ch.heading_path, anchor=ch.anchor)
                             for j, ch in enumerate(chunks)),
                parser_id="tavily", parser_version="bench")
            packet = store.put(project_id=PROJECT, parsed=parsed,
                               sha256=hashlib.sha256(item["text"].encode()).hexdigest(),
                               content=item["text"].encode(),
                               now=datetime(2026, 10, 19, tzinfo=UTC), owner=OWNER)
            vi.write(packet.material_id)
            url2mid[item["url"]] = packet.material_id
        conn.commit()
        chars = sum(len(i["text"]) for i in items)
        print(f"  {label:20} {len(items):>2} 份 · {chars:>7} 字 · {nblocks:>4} 块")
        corpus[label] = {"items": items, "conn": conn, "url2mid": url2mid, "blocks": nblocks}

    print("\n检索对照：")
    print(f"  {'口径':22}{'词存在':>9}{'FTS命中':>10}{'混合命中':>10}{'名次均':>8}{'第1名':>7}")
    for label in corpus:
        items = corpus[label]["items"]
        conn = corpus[label]["conn"]
        url2mid = corpus[label]["url2mid"]
        have = {
            mid: set(tokens(it["text"]))
            for it, mid in zip(items, url2mid.values(), strict=True)
        }
        exist = sum(1 for w, q, u in queries if w in have.get(url2mid.get(u, ""), ()))
        for kind, ret in (("FTS5", SqliteFtsRetriever(conn)),
                          ("混合", SqliteHybridRetriever(conn, embedder=embedder))):
            hits = 0
            ranks = []
            for w, _q, u in queries:
                mid = url2mid.get(u)
                if mid is None or w not in have.get(mid, ()):
                    continue
                res = ret.search(project_id=PROJECT, query=w, limit=5, owner=OWNER)
                ids = [h.material_id for h in res]
                if mid in ids:
                    hits += 1
                    ranks.append(ids.index(mid) + 1)
            mr = sum(ranks)/len(ranks) if ranks else 0
            if kind == "FTS5":
                fts = (hits, len(ranks), mr, sum(1 for r in ranks if r == 1))
            else:
                print(f"  {label:22}{exist:>4}/{len(queries):<4}{fts[0]:>4}/{fts[1]:<5}"
                      f"{hits:>4}/{len(ranks):<5}{mr:>8.2f}{sum(1 for r in ranks if r==1):>7}")
        conn.close()

if __name__ == "__main__":
    main()
