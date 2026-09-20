"""SSE 事件推送。"""

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

from web_api.dto import event_to_dto


async def event_stream(request: Request, round_id: str | None = None) -> StreamingResponse:
    runtime = request.app.state.runtime

    async def generator():
        events = runtime.event_log.read_by_round(round_id)
        last_seen_id = events[-1].event_id if events else None
        for event in events:
            dto = event_to_dto(event)
            yield f"data: {json.dumps(dto.model_dump(), ensure_ascii=False)}\n\n"

        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)
            all_events = runtime.event_log.read_by_round(round_id)
            new_events = []
            if last_seen_id is None:
                new_events = all_events
            else:
                found = False
                for e in all_events:
                    if found:
                        new_events.append(e)
                    elif e.event_id == last_seen_id:
                        found = True
            for event in new_events:
                dto = event_to_dto(event)
                yield f"data: {json.dumps(dto.model_dump(), ensure_ascii=False)}\n\n"
            if new_events:
                last_seen_id = new_events[-1].event_id

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
