"""英文原件与工厂转述的冲突比对与见证员确认。

只有会改变取样的分歧才列为冲突；每条冲突指明受影响的那一处说法、双方原文与
默认采用方。见证员确认前，受影响批的计划只给草案、不得据此封样。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sampling.instructions import OrderRequirement


@dataclass(frozen=True)
class Conflict:
    id: str
    topic: str                     # 分歧主题
    affected_word: str             # 受影响的一处说法
    buyer: str                     # 英文原件说法
    factory: str                   # 工厂转述说法
    sampling_impact: str           # 对取样的改变
    default_side: str              # 默认采用方（买家原件）
    refs: tuple[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "affected_word": self.affected_word,
            "buyer": self.buyer,
            "factory": self.factory,
            "sampling_impact": self.sampling_impact,
            "default_side": self.default_side,
            "refs": list(self.refs),
            "confirmed": False,
        }


def build_conflicts(orders: list[OrderRequirement]) -> list[Conflict]:
    by_id = {o.id: o for o in orders}
    o1, o2 = by_id["O1"], by_id["O2"]
    return [
        Conflict(
            id="C1",
            topic="取样单位（数量针对的对象）",
            affected_word="each lot / 每个包装规格",
            buyer="draw three intact panels from each lot：每批三件，批内分箱",
            factory="按包装规格各取三片：每个包装规格各三件",
            sampling_impact="若按工厂说法，标准盒与小盒各抽三件，抽样件数翻倍且改变分箱口径",
            default_side="买家原件 O1：每批三件",
            refs=("O1", "O2"),
        ),
        Conflict(
            id="C2",
            topic="透湿与剥离是否共享同一来源",
            affected_word="one parent panel for both / 各用一件",
            buyer="Use one parent panel for both MV and peel：两检测同一母件（另取小片）",
            factory="透湿与剥离可各用一件：拆成两个独立母件",
            sampling_impact="工厂说法多占一件完整件，并破坏买家要求的同源对照",
            default_side="买家原件 O1：同一母件、独立小片",
            refs=("O1", "O2"),
        ),
        Conflict(
            id="C3",
            topic="复测留样形态与封存时机",
            affected_word="one whole panel sealed in reserve / 留其中一片",
            buyer="整件原封预留，首次取样即另封，仅在触发时动用",
            factory="留其中一片备复测：预留被降格为小片",
            sampling_impact="小片不满足实验室整件封签要求，开封后不能再作完好备件，复测留样失效",
            default_side="买家原件 O1：整件另封预留",
            refs=("O1", "O2"),
        ),
    ]


class Store:
    """见证员确认的持久化（默认写入仓库外的工作目录，不污染原始资料）。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def list(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} 应为确认记录对象")
        return data

    def is_confirmed(self, conflict_id: str) -> bool:
        return conflict_id in self.list()

    def all_confirmed(self, conflicts: list[Conflict]) -> bool:
        return all(self.is_confirmed(c.id) for c in conflicts)

    def confirm(
        self,
        conflict_id: str,
        witness: str,
        side: str = "buyer",
        note: str = "",
    ) -> dict[str, Any]:
        conflicts_ids = {"C1", "C2", "C3"}
        if conflict_id not in conflicts_ids:
            raise KeyError(f"未知冲突编号：{conflict_id}")
        data = self.list()
        record = {
            "conflict": conflict_id,
            "witness": witness,
            "side": side,
            "note": note,
        }
        data[conflict_id] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        return record

    def get(self, conflict_id: str) -> Optional[dict[str, Any]]:
        return self.list().get(conflict_id)
