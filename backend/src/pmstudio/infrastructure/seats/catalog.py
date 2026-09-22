"""席位目录：v1 的静态只读配置，用 TOML 读（stdlib tomllib，不加依赖）。

提名规则（`04-未决.md` C1 的建议版）：通用组（always_on）每次都在；触发组按关键词命中。
用户可以在开场卡上删掉任何一个。
"""

import tomllib
from pathlib import Path

from pmstudio.application.ports import SeatDefinition


class TomlSeatCatalog:
    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._seats: list[SeatDefinition] = []
        for path in sorted(self._directory.glob("*.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            self._seats.append(
                SeatDefinition(
                    seat_id=data["seat_id"],
                    title=data["title"],
                    focus=data["focus"],
                    trigger_keywords=tuple(data.get("trigger_keywords", ())),
                    always_on=bool(data.get("always_on", False)),
                )
            )
        self.order: list[str] = [seat.seat_id for seat in self._seats]

    def all(self) -> tuple[SeatDefinition, ...]:
        return tuple(self._seats)

    def nominate(self, *, user_input: str) -> tuple[SeatDefinition, ...]:
        return tuple(
            seat
            for seat in self._seats
            if seat.always_on
            or any(keyword in user_input for keyword in seat.trigger_keywords)
        )

    def by_id(self, seat_id: str) -> SeatDefinition:
        for seat in self._seats:
            if seat.seat_id == seat_id:
                return seat
        raise KeyError(seat_id)
