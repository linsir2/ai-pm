"""断点续传的端到端证据：**换一个进程**（用同一个 db 文件重建容器）接着跑。

不重跑已交回的席位、不重复编号、只多一版——这是"进度是领域事实 + 幂等重放"的验收
（计划 §3.7 / §3.11），也是不挂 checkpointer 的底气。
"""

from tests.e2e.conftest import build_app
from tests.e2e.test_closed_loop import CLAIMS_REPLY, DRAFT_REPLY, INPUT, SEAT_REPLY
from tests.support.api_client import async_client

FIRST_SEAT = "seat.data_scale"
SECOND_SEAT = "seat.permissions"
OPENING = {"seats": [FIRST_SEAT, SECOND_SEAT], "claim_edits": []}


async def _create_project(client, *, title: str = "订单中台") -> str:  # type: ignore[no-untyped-def]
    response = await client.post("/api/projects", json={"title": title})
    assert response.status_code == 201
    return response.json()["project_id"]


async def _start_round(client, project_id: str):  # type: ignore[no-untyped-def]
    response = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": INPUT}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _opinion_eids(round_body: dict) -> list[str]:
    return [
        opinion["eid"]
        for packet in round_body["seat_packets"]
        for opinion in packet["opinions"]
        if opinion["eid"] is not None
    ]


async def test_resume_after_process_restart_without_redoing_finished_seats(tmp_path):
    """进程 1：A 交回、B 崩（整轮 failed，但 A 的意见留着）。进程 2：只补 B，跑完。"""

    first_app, _ = build_app(
        tmp_path,
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={FIRST_SEAT: SEAT_REPLY},  # 只给 A 配脚本 → B 会失败
        first_admin=True,
    )
    async with async_client(first_app) as client:
        project_id = await _create_project(client)
        started = await _start_round(client, project_id)
        crashed = await client.post(
            f"/api/rounds/{started['round_id']}/opening", json=OPENING
        )
        assert crashed.status_code == 502
        round_body = (await client.get(f"/api/rounds/{started['round_id']}")).json()
        assert round_body["step"] == "failed"
        assert [packet["status"] for packet in round_body["seat_packets"]] == [
            "completed",
            "pending",
        ]
        document = (await client.get(f"/api/projects/{project_id}/document")).json()
        assert document["version_no"] == 1
        round_id = started["round_id"]

    # —— 换进程：同一个 db 文件，补上 B 的脚本 ——
    second_app, gateway = build_app(
        tmp_path,
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={FIRST_SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY},
        first_admin=True,
    )
    async with async_client(second_app) as client:
        resumed = await client.post(f"/api/rounds/{round_id}/retry")
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()

        assert body["waiting_on"] == "draft_card"
        assert [packet["status"] for packet in body["seat_packets"]] == [
            "completed",
            "completed",
        ]
        assert body["seat_round_count"] == 1  # 还是同一轮席位，没有偷偷重开

        eids = _opinion_eids(body)
        assert eids  # 两个席位各交回了判断
        assert len(eids) == len(set(eids))  # 不重号

        accepted = [
            proposal["proposal_id"]
            for proposal in body["draft"]["proposals"]
            if proposal["origin_type"] != "proposed"
        ]
        confirmed = await client.post(
            f"/api/rounds/{round_id}/confirm", json={"accepted": accepted}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["version_no"] == 2
        workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
        assert workspace["document"]["version_no"] == 2  # 只多一版

    # 只补了缺的那个席位：第二个进程只调用过一次席位模型
    seat_calls = [
        request.metadata["seat_id"]
        for request in gateway.requests
        if request.purpose == "seat_review"
    ]
    assert seat_calls == [SECOND_SEAT]


async def test_resume_after_a_crash_between_join_and_reporting(tmp_path):
    """更狠的一刀：席位全交回、编号也分配了，但整合那步挂了；重启后必须复用编号。"""

    first_app, _ = build_app(
        tmp_path,
        replies={"claims": CLAIMS_REPLY},  # 故意不给 integration 脚本
        seat_replies={FIRST_SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY},
        first_admin=True,
    )
    async with async_client(first_app) as client:
        project_id = await _create_project(client)
        started = await _start_round(client, project_id)
        failed = await client.post(
            f"/api/rounds/{started['round_id']}/opening", json=OPENING
        )
        assert failed.status_code == 502
        before = (await client.get(f"/api/rounds/{started['round_id']}")).json()
        assert before["step"] == "failed"
        assert [packet["status"] for packet in before["seat_packets"]] == [
            "completed",
            "completed",
        ]
        eids_before = sorted(_opinion_eids(before))
        assert eids_before
        round_id = started["round_id"]

    second_app, _ = build_app(
        tmp_path,
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={FIRST_SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY},
        first_admin=True,
    )
    async with async_client(second_app) as client:
        resumed = await client.post(f"/api/rounds/{round_id}/retry")
        assert resumed.status_code == 200, resumed.text
        after = resumed.json()

        assert after["waiting_on"] == "draft_card"
        assert sorted(_opinion_eids(after)) == eids_before  # 编号被复用，没有重号
        assert after["seat_round_count"] == 1
        assert project_id  # 同一个项目
