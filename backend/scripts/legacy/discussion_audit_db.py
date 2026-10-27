"""讨论区数据闭环检查（M9 真跑验收 P0-C3）：只读，任一库副本可跑。

查的是"逻辑闭环"——每一张收下的卡都能顺着链走到账本，每一条账本 SIMULATED
都有来处；断在哪就报哪一行，不猜原因：

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 python3 scripts/discussion_audit_db.py <db文件>
```

退出码非 0 = 有断裂，打印全部断裂。讨论表不存在的老库（M9 之前）直接报一行跳过。
"""

import json
import sqlite3
import sys


def fail(out: list[str], message: str) -> None:
    out.append(message)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：discussion_audit_db.py <db文件>", flush=True)
        return 2
    db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    broken: list[str] = []

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "discussions" not in tables:
        print("没有 discussions 表（M9 之前的库）：跳过", flush=True)
        return 0

    ledgers: dict[str, dict[str, dict]] = {}
    for project_id, payload in db.execute("SELECT project_id, payload FROM evidence_ledgers"):
        try:
            body = json.loads(payload)
            # 行内存的是裸 list（`ledger_to_json` 即 evidences 数组）；容个错，dict 形也认
            items = body if isinstance(body, list) else body.get("evidences", [])
        except (json.JSONDecodeError, AttributeError):
            fail(broken, f"账本读不出 JSON：project={project_id}")
            continue
        ledgers[project_id] = {item["eid"]: item for item in items if "eid" in item}

    materials = {
        row[0]: row[1]
        for row in db.execute("SELECT material_id, source_ref FROM materials")
    }
    material_refs = {ref for ref in materials.values()}

    threads = list(
        db.execute(
            "SELECT discussion_id, project_id, status, payload FROM discussions"
        )
    )
    print(f"线程 {len(threads)} 条，材料 {len(materials)} 行", flush=True)
    for did, project_id, status, payload in threads:
        if status not in ("open", "closed"):
            fail(broken, f"{did}：status 非法：{status!r}")
        try:
            thread = json.loads(payload)
        except json.JSONDecodeError:
            fail(broken, f"{did}：payload 不是 JSON")
            continue
        for key in ("topic", "role_id", "role_title", "turns", "cards"):
            if key not in thread:
                fail(broken, f"{did}：payload 缺键 {key}")
        turns = thread.get("turns", [])
        for position, turn in enumerate(turns):
            if turn.get("index") != position:
                fail(broken, f"{did}：turn 索引不连续（第 {position} 条 index={turn.get('index')})")
            want_role = "user" if position % 2 == 0 else "assistant"
            if turn.get("role") != want_role:
                fail(broken, f"{did}：turn 交替断了（第 {position} 条是 {turn.get('role')})")
        known = set(ledgers.get(project_id, {}))
        for turn in turns:
            for ref in turn.get("based_on", []):
                if ref not in known:
                    fail(broken, f"{did}：turn{turn.get('index')} 引了账本外的编号 {ref}")
        for card in thread.get("cards", []):
            if card.get("grounding") == "document" and not card.get("based_on"):
                fail(broken, f"{did}：{card.get('card_id')} 标 document 却没依据")
            if card.get("grounding") == "invented" and card.get("based_on"):
                fail(broken, f"{did}：{card.get('card_id')} 标 invented 却带了依据")
            for ref in card.get("based_on", []):
                if ref not in known:
                    fail(broken, f"{did}：{card.get('card_id')} 引了账本外的编号 {ref}")
        # 收下为材料的卡片：新行（M12）在 kept_ref 稀疏映射里；老行在 `kept` map 里。
        new_shape = any(key in thread for key in ("states", "kept_as", "kept_ref"))
        kept_refs = thread.get("kept_ref") if new_shape else thread.get("kept")
        for card_id, material_id in (kept_refs or {}).items():
            if material_id not in materials:
                fail(broken, f"{did}：{card_id} 的 kept_ref {material_id} 在材料库里没有")
            want_ref = f"discussion:{did}#{card_id}"
            if materials.get(material_id) != want_ref:
                fail(
                    broken,
                    f"{did}：{card_id} 的材料 source_ref 是 {materials.get(material_id)!r}，"
                    f"应为 {want_ref!r}（收下链对不上）",
                )

    # 反方向：账本里每条 SIMULATED discussion 来路都必须有材料行＋线程＋卡片
    for _project_id, entries in ledgers.items():
        for eid, item in entries.items():
            if item.get("kind") != "simulated":
                continue
            ref = (item.get("source") or {}).get("external_ref") or ""
            if not ref.startswith("discussion:"):
                continue
            base = ref.rsplit("#c", 1)[0] if "#c" in ref else ref
            if base not in material_refs:
                fail(broken, f"{eid}：{ref} 在材料库里没有对应的 source_ref")
                continue
            try:
                thread_part, card_id = base.removeprefix("discussion:").split("#")
            except ValueError:
                fail(broken, f"{eid}：{ref} 形状不对，拆不出线程#卡片")
                continue
            row = db.execute(
                "SELECT payload FROM discussions WHERE discussion_id = ?", (thread_part,)
            ).fetchone()
            if row is None:
                fail(broken, f"{eid}：{ref} 指的线程 {thread_part} 不存在")
                continue
            cards = {c.get("card_id") for c in json.loads(row[0]).get("cards", [])}
            if card_id not in cards:
                fail(broken, f"{eid}：{ref} 指的卡片 {card_id} 在线程里没有")

    # 孤儿：origin=discussion 的材料行，source_ref 必须能拆回某条线程的某张卡
    for material_id, source_ref in db.execute(
        "SELECT material_id, source_ref FROM materials WHERE origin = 'discussion'"
    ):
        try:
            thread_part, card_id = source_ref.removeprefix("discussion:").split("#")
        except ValueError:
            fail(broken, f"材料 {material_id}：source_ref {source_ref!r} 形状不对")
            continue
        row = db.execute(
            "SELECT payload FROM discussions WHERE discussion_id = ?", (thread_part,)
        ).fetchone()
        if row is None:
            fail(broken, f"材料 {material_id}：线程 {thread_part} 不存在（孤儿材料）")

    if broken:
        for line in broken:
            print(f"断裂：{line}", flush=True)
        return 1
    print("OK： turns 交替／编号可查／收下链／账本回链／孤儿检查全过", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
