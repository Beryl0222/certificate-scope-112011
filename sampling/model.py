"""领域数据结构与常量。

所有结构均为不可变数据对象，附带 ``to_dict`` 便于 JSON 输出；解释性文字同时保留
对应原件编号（``refs``），使每个结论都能回到原文。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


# -- 检测/委托用途 -------------------------------------------------------------

MV = "MV"          # 透湿
PEEL = "peel"      # 剥离
VISUAL = "visual"  # 整件外观
RESERVE = "reserve"  # 复测预留

ROLE_LABELS = {
    MV: "透湿(MV)",
    PEEL: "剥离(peel)",
    VISUAL: "整件外观",
    RESERVE: "复测预留",
}

# -- 小片/母件/封签实物状态 -----------------------------------------------------

# 小片状态
PIECE_AVAILABLE = "可用"          # 仍在母件上、未开封未消耗
PIECE_CUT = "已裁切"              # 已从母件切分
PIECE_CONSUMED = "已消耗"         # 破坏性检测消耗（如 MV）
PIECE_OPENED = "已拆封"           # 经过拆包，不再具备完好身份
PIECE_USED_TESTED = "已用于检测"  # 已用于某项检测（未必消耗）

# 母件状态
PARENT_INTACT = "完整未开封"
PARENT_CUT = "已裁切"
PARENT_OPENED = "已拆封"
PARENT_CONSUMED = "已含消耗片"

# 封签形态（实验室只收自己认可的形态）
SEAL_WHOLE = "整件封签"
SEAL_PIECES = "小片封签"
SEAL_INFERRED = "按实际封样内容推断"

# -- 缺件/拒收/失效原因码 -------------------------------------------------------

REASON = {
    "SHORT_VISUAL": "缺整件外观样（另要求的整件外观项不能用已裁切母件代交）",
    "SHORT_SURFACE": "无可接受表面的母件（内层表面禁用）",
    "SHORT_CARTONS": "可用箱数不足，无法满足分箱取样",
    "CONTROL_LOST": "同源对照不可恢复：相关检测一份样已毁损且无可接受同源余样，合并判定缺样",
    "RESERVE_NOT_PAIRED": "预留样无法与原对照面配对，不能单独动用",
    "SURFACE_NONCONFORM": "取样表面不符合外层限定，结果对本次委托无效",
    "SEAL_FORM": "封签形态不被该实验室认可",
    "OPENED_RESERVE": "样件已开封，不能再作为完好备件",
    "NO_INTACT_WITNESS": "裁切前未完成完整状态身份与外观见证",
    "PIECE_ALREADY_USED": "该片已拆封或消耗，不能按图上余料再分给他用",
    "REWITNESS": "须重新见证成组取样（从别箱凑数量无意义）",
    "CONFLICT_UNCONFIRMED": "英文原件与工厂转述冲突尚未经见证员确认",
    "DUPLICATE_SLIP": "西实验室要求另寄原件身份见证副联",
}


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


@dataclass(frozen=True)
class Issue:
    """一条可回溯的问题/原因说明。"""

    code: str
    detail: str
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": REASON.get(self.code, self.code),
            "detail": self.detail,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class QuantityTarget:
    """数量针对的对象与取样单位。"""

    count: int
    object_text: str          # 数量针对的对象原文/说法
    unit_text: str            # 取样单位（每批/每个包装规格）
    per_lot: bool             # 是否按批计数
    separate_cartons: bool    # 是否要求来自不同箱
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceBinding:
    """两项检测之间的同源/独立关系。"""

    roles: tuple[str, str]
    shared_parent: bool       # 是否允许/要求同一母件（同一件）
    same_piece_allowed: bool  # 是否允许同一片（破坏性项目必为否）
    adjacency_required: bool  # 是否要求相邻片
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReserveRule:
    """复测预留与触发规则。"""

    whole_panel_sealed: bool          # 预留必须是整件原封
    trigger_events: tuple[str, ...]   # 允许动用预留的事件
    non_triggers: tuple[str, ...]     # 明确不触发的情形
    paired_with_original: bool        # 复测须与原对照面配对
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
