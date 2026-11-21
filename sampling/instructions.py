"""解析买家委托、工厂转述与后续说明。

针对本委托语料的中英混合说法做规则化解读，逐条把「数量针对的对象、取样单位、
共享同一来源、必须用独立试样、留样形态与触发条件」结构化，并在每条结论上保留
原件编号，保证解读可回到原文。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sampling.model import (
    MV,
    PEEL,
    VISUAL,
    QuantityTarget,
    ReserveRule,
    SourceBinding,
)

# 复测触发事件（仅让预留样进入相应准备状态，不自动消耗）
TRIGGER_SEAL_BREAK = "运输封签破损"
TRIGGER_MV_NO_READING = "透湿无读数"
# 明确不触发复测的情形
NONTRIGGER_LOW_VALID = "读数偏低但有完整有效读数"
NONTRIGGER_VALID_FAIL = "已测不合格但结果有效"


@dataclass(frozen=True)
class SurfaceRule:
    """取样表面限定（可按批）。"""

    lot: str
    allowed: str                 # 允许的表面登记值
    meaning: str                 # 买家对该说法的界定原文
    banned: tuple[str, ...]      # 禁用表面
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot": self.lot,
            "allowed": self.allowed,
            "meaning": self.meaning,
            "banned": list(self.banned),
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class FreshSetRule:
    """对照不可恢复时重新取得整套样件的规则。"""

    lot: str
    condition: str
    fresh_set: bool
    visual_still_requested: bool
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot": self.lot,
            "condition": self.condition,
            "fresh_set": self.fresh_set,
            "visual_still_requested": self.visual_still_requested,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class OrderRequirement:
    """单条来件的结构化解读。"""

    id: str
    time: str
    sender: str
    cites: str
    raw: str
    quantity: QuantityTarget | None = None
    bindings: tuple[SourceBinding, ...] = ()
    roles: tuple[str, ...] = ()
    reserve: ReserveRule | None = None
    surface: SurfaceRule | None = None
    fresh_set: FreshSetRule | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "时间": self.time,
            "来自": self.sender,
            "引用": self.cites,
            "原文": self.raw,
            "quantity": self.quantity.to_dict() if self.quantity else None,
            "bindings": [b.to_dict() for b in self.bindings],
            "roles": list(self.roles),
            "reserve": self.reserve.to_dict() if self.reserve else None,
            "surface": self.surface.to_dict() if self.surface else None,
            "fresh_set": self.fresh_set.to_dict() if self.fresh_set else None,
        }


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def parse_orders(records: list[dict]) -> list[OrderRequirement]:
    """按来件逐条解析，保持录入顺序。"""
    parsed: list[OrderRequirement] = []
    for row in records:
        oid, text = row["id"], row["原文"]
        quantity = None
        bindings: list[SourceBinding] = []
        roles: list[str] = []
        reserve = None
        surface = None
        fresh_set = None

        if oid == "O1":
            quantity = QuantityTarget(
                count=3,
                object_text="intact panels（完整件，整件）",
                unit_text="each lot（每批）",
                per_lot=True,
                separate_cartons="separate cartons" in text,
                refs=("O1",),
            )
            # 同一母件同时承担透湿与剥离；是否同小片由实验室规则进一步约束
            bindings.append(SourceBinding(
                roles=(MV, PEEL),
                shared_parent=True,
                same_piece_allowed=False,
                adjacency_required=False,
                refs=("O1",),
            ))
            roles = [MV, PEEL, VISUAL]
            reserve = ReserveRule(
                whole_panel_sealed=True,
                trigger_events=(),
                non_triggers=(),
                paired_with_original=False,
                refs=("O1",),
            )
        elif oid == "O2":
            quantity = QuantityTarget(
                count=3,
                object_text="片（工厂转述，未保留整件含义）",
                unit_text="每个包装规格",
                per_lot=False,
                separate_cartons=False,
                refs=("O2",),
            )
            # 工厂：透湿与剥离各用一件 → 放弃同一来源
            bindings.append(SourceBinding(
                roles=(MV, PEEL),
                shared_parent=False,
                same_piece_allowed=False,
                adjacency_required=False,
                refs=("O2",),
            ))
            roles = [MV, PEEL, VISUAL]
            # 留其中一片：预留被降格为小片，而非整件原封
            reserve = ReserveRule(
                whole_panel_sealed=False,
                trigger_events=(),
                non_triggers=(),
                paired_with_original=False,
                refs=("O2",),
            )
        elif oid == "O3":
            triggers = []
            non_triggers = []
            if _has(text, "seal is broken", "seal failure"):
                triggers.append(TRIGGER_SEAL_BREAK)
            if _has(text, "MV has no reading", "no reading"):
                triggers.append(TRIGGER_MV_NO_READING)
            if "low but valid" in text:
                non_triggers.append(NONTRIGGER_LOW_VALID)
            reserve = ReserveRule(
                whole_panel_sealed=True,
                trigger_events=tuple(triggers),
                non_triggers=tuple(non_triggers),
                paired_with_original="paired with the original control" in text,
                refs=("O3",),
            )
        elif oid == "O4":
            surface = SurfaceRule(
                lot="J17",
                allowed="朝开口",
                meaning="film facing the carton opening（朝向纸箱开口的膜面，非纸箱顶层）",
                banned=("朝内壁",),
                refs=("O4",),
            )
        elif oid == "O5":
            fresh_set = FreshSetRule(
                lot="J18",
                condition="control surface cannot be recovered（对照面无法恢复）",
                fresh_set=True,
                visual_still_requested="remains requested" in text,
                refs=("O5",),
            )

        parsed.append(OrderRequirement(
            id=oid,
            time=row["时间"],
            sender=row["来自"],
            cites=row.get("引用", ""),
            raw=text,
            quantity=quantity,
            bindings=tuple(bindings),
            roles=tuple(roles),
            reserve=reserve,
            surface=surface,
            fresh_set=fresh_set,
        ))
    return parsed


def classify_trigger(text: str) -> tuple[str, bool]:
    """把交接/检测记录原文分类为 (事件类别, 是否构成复测触发)。

    买家文件分别处理破损、试验无结果与已测不合格：
    只有运输封签破损与透湿无读数触发预留样进入准备状态；
    读数偏低但有效、已测不合格均不触发。
    """
    t = text.lower()
    # 运输封签破损/经过拆包：O3 列明的触发事件
    if any(w in text for w in ("拆包", "拆封", "破损", "封签破", "seal is broken", "seal failure")):
        if "未开封" not in text:
            return TRIGGER_SEAL_BREAK, True
    if "无读数" in text or "no reading" in t:
        return TRIGGER_MV_NO_READING, True
    if "过低" in text or "偏低" in text:
        if "完整读数" in text or "有效" in text or "valid" in t:
            return NONTRIGGER_LOW_VALID, False
    if "不合格" in text:
        return NONTRIGGER_VALID_FAIL, False
    return "其他记录", False
