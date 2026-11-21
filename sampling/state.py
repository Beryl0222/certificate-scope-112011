"""由库存登记与交接记录重建实物状态。

重建原则（均只依据本地资料，不臆测）：

- 小片状态优先级：已消耗 > 已拆封 > 已用于检测 > 已裁切 > 可用；
- 母件在其任一小片被切/封/测前，若出现裁切或检测动作但没有「完整件先见证」
  记录，标记裁切门缺陷（L5）；
- 封签按实际封样内容占用小片：封签内的小片不得再按图上余料分给他用；
- 已拆封或消耗的试样同样不可再分配；
- 无独立封签动作却出现检测记录时，按实际内容推断封签并显式标注为推断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sampling.model import (
    PIECE_AVAILABLE,
    PIECE_CONSUMED,
    PIECE_CUT,
    PIECE_OPENED,
    PIECE_USED_TESTED,
    PARENT_CONSUMED,
    PARENT_CUT,
    PARENT_INTACT,
    PARENT_OPENED,
    SEAL_INFERRED,
    SEAL_PIECES,
    SEAL_WHOLE,
)


@dataclass(frozen=True)
class PieceView:
    name: str
    state: str
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "refs": list(self.refs)}


@dataclass(frozen=True)
class SealView:
    seal: str
    parent: str
    form: str                       # 整件封签 / 小片封签 / 推断
    pieces: tuple[str, ...]
    lab_hint: str                   # 由原文识别的目标实验室
    inferred: bool
    text: str = ""                  # 封样/签收记录原文，用于识别用途
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seal": self.seal,
            "parent": self.parent,
            "form": self.form,
            "pieces": list(self.pieces),
            "lab_hint": self.lab_hint,
            "inferred": self.inferred,
            "text": self.text,
            "refs": list(self.refs),
        }


@dataclass
class ParentView:
    id: str
    lot: str
    carton: str
    package: str
    surface: str
    cuttable: tuple[str, ...]
    pieces: dict[str, PieceView] = field(default_factory=dict)
    seals: list[SealView] = field(default_factory=list)
    intact_witness_before_cut: bool = False
    events: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        states = {p.state for p in self.pieces.values()}
        if PIECE_CONSUMED in states:
            return PARENT_CONSUMED
        if PIECE_OPENED in states:
            return PARENT_OPENED
        if states & {PIECE_CUT, PIECE_USED_TESTED}:
            return PARENT_CUT
        return PARENT_INTACT

    def usable_pieces(self) -> list[str]:
        return [n for n, p in self.pieces.items() if p.state == PIECE_AVAILABLE]

    def is_intact(self) -> bool:
        return self.state == PARENT_INTACT

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "批": self.lot,
            "箱": self.carton,
            "包装": self.package,
            "表面": self.surface,
            "state": self.state,
            "intact_witness_before_cut": self.intact_witness_before_cut,
            "pieces": [self.pieces[n].to_dict() for n in self.cuttable],
            "seals": [s.to_dict() for s in self.seals],
            "events": list(self.events),
        }


def _lab_hint(text: str) -> str:
    if "东实验室" in text:
        return "东实验室"
    if "西实验室" in text:
        return "西实验室"
    return ""


def build_state(
    inventory: list[dict],
    handover: list[dict],
) -> dict[str, ParentView]:
    parents: dict[str, ParentView] = {}
    for row in inventory:
        parents[row["id"]] = ParentView(
            id=row["id"],
            lot=row["批"],
            carton=row["箱"],
            package=row["包装"],
            surface=row["表面"],
            cuttable=tuple(row["可切片"]),
            pieces={
                name: PieceView(name=name, state=PIECE_AVAILABLE)
                for name in row["可切片"]
            },
        )

    def mark(piece_name: str, new_state: str, ref: str) -> None:
        current = parent.pieces[piece_name].state
        # 状态优先级：消耗 > 拆封 > 已测 > 裁切 > 可用
        rank = {
            PIECE_AVAILABLE: 0,
            PIECE_CUT: 1,
            PIECE_USED_TESTED: 2,
            PIECE_OPENED: 3,
            PIECE_CONSUMED: 4,
        }
        refs = set(parent.pieces[piece_name].refs)
        refs.add(ref)
        if rank[new_state] >= rank[current]:
            parent.pieces[piece_name] = PieceView(
                piece_name, new_state, tuple(sorted(refs))
            )
        else:
            parent.pieces[piece_name] = PieceView(
                piece_name, current, tuple(sorted(refs))
            )

    known_seals: set[str] = {
        h["封签"] for h in handover if h["动作"] == "封签" and h["封签"]
    }

    for h in handover:
        parent = parents[h["母件"]]
        ref = h["id"]
        parent.events.append(ref)
        action, pieces, text, seal = h["动作"], h["片"], h["原文"], h["封签"]

        if action == "封签":
            form = SEAL_WHOLE if not pieces else SEAL_PIECES
            parent.seals.append(SealView(
                seal=seal,
                parent=parent.id,
                form=form,
                pieces=tuple(pieces),
                lab_hint=_lab_hint(text),
                inferred=False,
                text=text,
                refs=(ref,),
            ))
            for name in pieces:
                mark(name, PIECE_CUT, ref)
            if "完整" in text:
                parent.intact_witness_before_cut = True
        elif action == "签收":
            # 签收不改变片状态；若该封签从未有封签动作，按实际内容补推断视图
            if seal and seal not in {s.seal for s in parent.seals}:
                form = SEAL_WHOLE if not pieces else SEAL_PIECES
                parent.seals.append(SealView(
                    seal=seal,
                    parent=parent.id,
                    form=form,
                    pieces=tuple(pieces),
                    lab_hint=_lab_hint(text),
                    inferred=True,
                    text=text,
                    refs=(ref,),
                ))
        elif action == "已检测":
            # 检测隐含此前已裁切；MV 对应片按实验室约定记消耗，其余记已用于检测
            for name in pieces:
                if name == "a" and ("消耗" in text or "无读数" in text):
                    mark(name, PIECE_CONSUMED, ref)
                else:
                    mark(name, PIECE_USED_TESTED, ref)
            # 无封签动作的检测件：按实际封样内容推断封签
            if seal and seal not in {s.seal for s in parent.seals}:
                parent.seals.append(SealView(
                    seal=seal,
                    parent=parent.id,
                    form=SEAL_INFERRED,
                    pieces=tuple(pieces),
                    lab_hint=_lab_hint(text),
                    inferred=True,
                    text=text,
                    refs=(ref,),
                ))
            if "已见证完整件" in text:
                parent.intact_witness_before_cut = True
        elif action == "运输记录":
            for name in pieces:
                mark(name, PIECE_OPENED, ref)

    return parents
