"""溯源串联：原指令 → 取样证明 → 封签 → 检测用途。

支持按封签号、母件号或交接记录号查询某次已签收试样的完整链路，每一环都带原件
编号与原文，保证见证员可逐句核对。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sampling.instructions import OrderRequirement, classify_trigger
from sampling.state import ParentView


@dataclass(frozen=True)
class TraceNode:
    kind: str           # 指令 / 库存身份 / 封签 / 签收 / 检测 / 运输
    ref: str
    summary: str
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "summary": self.summary, "raw": self.raw}


def _orders_relevant(orders: list[OrderRequirement], lot: str) -> list[OrderRequirement]:
    relevant = []
    for o in orders:
        if o.id in {"O1", "O2", "O3"}:
            relevant.append(o)
        elif o.surface and o.surface.lot == lot:
            relevant.append(o)
        elif o.fresh_set and o.fresh_set.lot == lot:
            relevant.append(o)
    return relevant


def trace_ref(
    query: str,
    orders: list[OrderRequirement],
    inventory: list[dict],
    handover: list[dict],
    parents: dict[str, ParentView],
) -> dict[str, Any]:
    """按封签号/母件号/交接记录号串联一条完整链路。"""
    # 定位相关交接行
    rows = [
        h for h in handover
        if h["封签"] == query or h["母件"] == query or h["id"] == query
    ]
    if not rows:
        # 可能直接给了母件号但无交接记录
        if query in parents:
            rows = []
        else:
            return {"query": query, "found": False, "chain": []}

    parent_ids = sorted({h["母件"] for h in rows} | ({query} if query in parents else set()))
    lots = sorted({parents[pid].lot for pid in parent_ids if pid in parents})
    lot = lots[0] if lots else ""

    chain: list[TraceNode] = []

    # 1) 原指令层
    for o in _orders_relevant(orders, lot):
        chain.append(TraceNode(
            kind="指令",
            ref=o.id,
            summary=f"{o.sender}（引用 {o.cites or '—'}）",
            raw=o.raw,
        ))

    # 2) 库存身份层（来自哪个箱、哪个表面、包装、可切片）
    for row in inventory:
        if row["id"] in parent_ids:
            chain.append(TraceNode(
                kind="库存身份",
                ref=row["id"],
                summary=(
                    f"批 {row['批']} / {row['箱']} / {row['包装']} / "
                    f"表面 {row['表面']} / 可切片 {','.join(row['可切片'])}"
                ),
                raw="",
            ))

    # 3) 交接动作层（保持时间顺序）
    role_text = {
        "封签": "封签（取样证明）",
        "签收": "签收",
        "已检测": "检测",
        "运输记录": "运输",
    }
    for h in sorted(rows, key=lambda r: r["时间"]):
        event, is_trigger = classify_trigger(h["原文"])
        summary = (
            f"{role_text.get(h['动作'], h['动作'])} {h['时间']}，"
            f"封签 {h['封签'] or '—'}，片 {','.join(h['片']) or '整件'}"
        )
        if h["动作"] == "已检测" or h["动作"] == "运输记录":
            summary += f"；事件分类：{event}（{'构成复测触发' if is_trigger else '不触发复测'}）"
        chain.append(TraceNode(
            kind=role_text.get(h["动作"], h["动作"]),
            ref=h["id"],
            summary=summary,
            raw=h["原文"],
        ))

    # 4) 当前实物结论
    for pid in parent_ids:
        if pid not in parents:
            continue
        p = parents[pid]
        piece_summary = "；".join(
            f"{n}:{v.state}" for n, v in p.pieces.items()
        )
        chain.append(TraceNode(
            kind="当前实物状态",
            ref=pid,
            summary=f"母件状态 {p.state}；{piece_summary}",
            raw="",
        ))

    return {
        "query": query,
        "found": True,
        "lot": lot,
        "parent_ids": parent_ids,
        "seal_ids": sorted({h["封签"] for h in rows if h["封签"]}),
        "chain": [n.to_dict() for n in chain],
    }
