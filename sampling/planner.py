"""逐批生成可实际封样/收样的分配、缺件与原因、复测判定。

输入全部来自本地资料与见证员确认状态，输出仅描述建议与判定，不修改原始数据。
关键规则入口：

- 冲突未确认 → 整批只给草案并标注，不得据此封样；
- O4 表面限定按批生效，内层表面件即使已检测，结果对本次委托无效；
- 已封见证件按实际封样内容占用小片，已拆封/消耗片不再参与任何分配；
- 复测对照限同母件未用邻片（L4），对照毁损且无同源余样时合并判定缺样、
  重新见证成组取样，禁止从别箱凑数；
- 候选分配只使用完整未开封、表面合规且分箱不重复的母件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sampling.instructions import OrderRequirement
from sampling.labrules import LabRuleSet
from sampling.model import (
    MV,
    PEEL,
    VISUAL,
    RESERVE,
    ROLE_LABELS,
    Issue,
    PIECE_AVAILABLE,
    PIECE_CONSUMED,
    PIECE_OPENED,
    PIECE_USED_TESTED,
    PARENT_CUT,
    PARENT_CONSUMED,
    SEAL_WHOLE,
    SEAL_PIECES,
)
from sampling.reconcile import Conflict, Store
from sampling.state import ParentView


# 分配/判定的处置类别
KEEP = "可继续送检"          # 已封且合规
REDRAW = "须解封或重取"       # 实物不合规，需重取
INVALIDATED = "结果失效"      # 已做检测但结果对本次委托无效
PROPOSE = "建议新封"          # 尚未封样的候选分配
MISSING = "缺件"              # 无可用实物
READY = "预留进入准备状态"    # 复测触发但未动用


@dataclass(frozen=True)
class Allocation:
    kind: str                      # KEEP / REDRAW / INVALIDATED / PROPOSE / MISSING
    role: str                      # MV / peel / visual / reserve / 成套复测
    parent: Optional[str]
    carton: Optional[str]
    surface: Optional[str]
    pieces: tuple[str, ...]
    seal_form: Optional[str]
    seal: Optional[str]
    detail: str
    issues: tuple[Issue, ...] = ()
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "role": ROLE_LABELS.get(self.role, self.role),
            "parent": self.parent,
            "carton": self.carton,
            "surface": self.surface,
            "pieces": list(self.pieces),
            "seal_form": self.seal_form,
            "seal": self.seal,
            "detail": self.detail,
            "issues": [i.to_dict() for i in self.issues],
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class ReserveStatus:
    parent: Optional[str]
    seal: Optional[str]
    whole_sealed: bool
    triggered: bool
    trigger_events: tuple[str, ...]
    non_triggers: tuple[str, ...]
    armed: bool                    # 触发后是否仅进入准备状态（未自动开封）
    paired_control_available: Optional[bool]
    decision: str
    detail: str
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent": self.parent,
            "seal": self.seal,
            "whole_sealed": self.whole_sealed,
            "triggered": self.triggered,
            "trigger_events": list(self.trigger_events),
            "non_triggers": list(self.non_triggers),
            "armed_only": self.armed,
            "paired_control_available": self.paired_control_available,
            "decision": self.decision,
            "detail": self.detail,
            "refs": list(self.refs),
        }


@dataclass
class LotPlan:
    lot: str
    conflicts_blocking: list[dict[str, Any]]
    effective: dict[str, Any]
    allocations: list[Allocation] = field(default_factory=list)
    missing: list[Issue] = field(default_factory=list)
    reserve: Optional[ReserveStatus] = None
    repeat: Optional[dict[str, Any]] = None
    clarification_impact: list[dict[str, Any]] = field(default_factory=list)
    candidate_parents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot": self.lot,
            "draft_only": bool(self.conflicts_blocking),
            "conflicts_blocking": self.conflicts_blocking,
            "effective_requirement": self.effective,
            "allocations": [a.to_dict() for a in self.allocations],
            "missing": [i.to_dict() for i in self.missing],
            "reserve": self.reserve.to_dict() if self.reserve else None,
            "repeat": self.repeat,
            "clarification_impact": self.clarification_impact,
            "candidate_parents": self.candidate_parents,
        }


def effective_requirement(orders: list[OrderRequirement]) -> dict[str, Any]:
    """以买家原件 O1 为基线（冲突确认后采用）的有效委托要点。"""
    by_id = {o.id: o for o in orders}
    o1, o3 = by_id["O1"], by_id["O3"]
    return {
        "quantity_per_lot": 3,
        "unit": "每批三件完整件，来自不同箱",
        "shared_parent": {MV: True, PEEL: True},
        "independent_pieces": "MV 与剥离不得为同一小片（L1）",
        "reserve": "一件整件原封，首次取样即另封",
        "visual": "一件完整件裁切前外观检查，须整件封签",
        "reserve_triggers": list(o3.reserve.trigger_events),
        "reserve_non_triggers": list(o3.reserve.non_triggers),
        "repeat_paired": o3.reserve.paired_with_original,
        "refs": ["O1", "O3"],
    }


def _surface_allowed(orders: list[OrderRequirement], lot: str, surface: str) -> tuple[bool, Optional[str]]:
    for o in orders:
        if o.surface and o.surface.lot == lot:
            if surface in o.surface.banned:
                return False, o.id
            return surface == o.surface.allowed, o.id
    return True, None


def _issue(code: str, detail: str, refs: tuple[str, ...]) -> Issue:
    return Issue(code=code, detail=detail, refs=refs)


def build_lot_plan(
    lot: str,
    orders: list[OrderRequirement],
    lab: LabRuleSet,
    parents: dict[str, ParentView],
    conflicts: list[Conflict],
    store: Optional[Store] = None,
    handover: Optional[list[dict]] = None,
) -> LotPlan:
    blocking: list[dict[str, Any]] = []
    for c in conflicts:
        if store is None or not store.is_confirmed(c.id):
            blocking.append(c.to_dict())

    plan = LotPlan(
        lot=lot,
        conflicts_blocking=blocking,
        effective=effective_requirement(orders),
    )

    lot_parents = [p for p in parents.values() if p.lot == lot]
    by_id = {o.id: o for o in orders}

    # 检测/运输事件的触发分类：只认买家 O3 列明的事件，有效低值与有效不合格不触发
    from sampling.instructions import classify_trigger
    triggered_parents: set[str] = set()
    event_classification: dict[str, dict[str, tuple[str, bool]]] = {}
    if handover:
        for h in handover:
            if h["批"] != lot:
                continue
            kind, is_trigger = classify_trigger(h["原文"])
            event_classification.setdefault(h["母件"], {})[h["id"]] = (kind, is_trigger)
            if is_trigger:
                triggered_parents.add(h["母件"])

    # 触发事件与非触发事件：由父件检测状态与交接原文分类汇总
    reserve_parent = _find_reserve_parent(lot_parents)
    tested_parents = [p for p in lot_parents if p.state in {PARENT_CUT, PARENT_CONSUMED}]

    # ---- O4 澄清影响：逐件分类 ------------------------------------------
    surface_rule = next((o.surface for o in orders if o.surface and o.surface.lot == lot), None)
    if surface_rule:
        for p in lot_parents:
            ok, rule_ref = _surface_allowed(orders, lot, p.surface)
            if ok:
                if any(s.form == SEAL_WHOLE for s in p.seals) or p.is_intact():
                    plan.clarification_impact.append({
                        "parent": p.id,
                        "surface": p.surface,
                        "decision": KEEP,
                        "detail": "外层表面且封签/完整性满足要求，可继续送",
                        "refs": [rule_ref],
                    })
                continue
            # 不合规表面
            tested_pieces = [
                n for n, v in p.pieces.items()
                if v.state in {PIECE_USED_TESTED, PIECE_CONSUMED}
            ]
            if tested_pieces:
                plan.clarification_impact.append({
                    "parent": p.id,
                    "surface": p.surface,
                    "decision": INVALIDATED,
                    "detail": "内层表面已做检测，结果失去本次委托用途，不可补回原状",
                    "refs": [rule_ref],
                })
            else:
                plan.clarification_impact.append({
                    "parent": p.id,
                    "surface": p.surface,
                    "decision": REDRAW,
                    "detail": "内层表面件须解封重取（若已封）",
                    "refs": [rule_ref],
                })

    # ---- 已执行检测件的用途判定 -----------------------------------------
    for p in tested_parents:
        ok, rule_ref = _surface_allowed(orders, lot, p.surface)
        issues: list[Issue] = []
        if not ok:
            issues.append(_issue(
                "SURFACE_NONCONFORM",
                f"{p.id} 表面 {p.surface} 属禁用表面",
                (rule_ref,),
            ))
        if not p.intact_witness_before_cut and lab.intact_witness_required():
            issues.append(_issue(
                "NO_INTACT_WITNESS",
                f"{p.id} 裁切前未见完整状态身份与外观见证记录",
                ("L5",),
            ))
        piece_a = p.pieces.get("a")
        piece_b = p.pieces.get("b")
        piece_c = p.pieces.get("c")
        mv_state = piece_a.state if piece_a else PIECE_AVAILABLE
        peel_state = piece_b.state if piece_b else PIECE_AVAILABLE
        parent_triggered = p.id in triggered_parents
        event_notes = [
            f"{hid}:{kind}（{'触发' if trig else '不触发'}）"
            for hid, (kind, trig) in event_classification.get(p.id, {}).items()
        ]

        # 透湿位
        if mv_state in {PIECE_CONSUMED, PIECE_USED_TESTED}:
            kind = INVALIDATED if not ok else KEEP
            if parent_triggered:
                detail = "透湿 a 片已消耗且试验无结果，按 O3 触发复测（预留仅进入准备状态）"
            else:
                detail = "透湿 a 片已消耗；结果有效（含偏低但有效），不触发复测"
            plan.allocations.append(Allocation(
                kind=kind,
                role=MV,
                parent=p.id,
                carton=p.carton,
                surface=p.surface,
                pieces=("a",),
                seal_form=SEAL_PIECES,
                seal=next((s.seal for s in p.seals if "a" in s.pieces), None),
                detail=detail + ("；" + "，".join(event_notes) if event_notes else ""),
                issues=tuple(issues),
                refs=tuple(p.events),
            ))
        # 剥离位
        if peel_state in {PIECE_USED_TESTED, PIECE_CONSUMED}:
            plan.allocations.append(Allocation(
                kind=INVALIDATED if not ok else KEEP,
                role=PEEL,
                parent=p.id,
                carton=p.carton,
                surface=p.surface,
                pieces=("b",),
                seal_form=SEAL_PIECES,
                seal=next((s.seal for s in p.seals if "b" in s.pieces), None),
                detail="剥离已用 b 片" + ("；" + "，".join(event_notes) if event_notes else ""),
                issues=tuple(issues),
                refs=tuple(p.events),
            ))

        # 复测判定：仅在 O3 触发事件发生时评估同源对照（L4）
        if parent_triggered:
            adjacent = lab.is_adjacent("a", "b")
            usable_neighbor = (
                adjacent
                and piece_b is not None
                and piece_b.state == PIECE_AVAILABLE
            )
            c_state = piece_c.state if piece_c else PIECE_AVAILABLE
            c_non_adjacent = not lab.is_adjacent("a", "c")
            plan.repeat = _build_repeat(
                lot, p, usable_neighbor, c_state, c_non_adjacent, orders
            )

    # ---- 预留件 ---------------------------------------------------------
    plan.reserve = _build_reserve(
        lot_parents, orders, bool(triggered_parents), triggered_parents
    )

    # ---- 外观件（整件）--------------------------------------------------
    _evaluate_visual(plan, lot_parents, orders, lab)

    # ---- 候选完整件与新封分配 -------------------------------------------
    used_parents = {p.id for p in tested_parents}
    if reserve_parent:
        used_parents.add(reserve_parent.id)
    visual_parent = next(
        (p for p in lot_parents
         if any(s.form == SEAL_WHOLE for s in p.seals) and p.id not in used_parents),
        None,
    )
    if visual_parent:
        used_parents.add(visual_parent.id)

    candidates = [
        p for p in lot_parents
        if p.id not in used_parents
        and p.is_intact()
        and _surface_allowed(orders, lot, p.surface)[0]
    ]
    plan.candidate_parents = [p.id for p in candidates]
    _propose_allocations(plan, lot, candidates, orders, lab)

    # ---- 缺件汇总 -------------------------------------------------------
    _collect_missing(plan, lot, orders, lab)
    return plan


def _find_reserve_parent(lot_parents: list[ParentView]) -> Optional[ParentView]:
    for p in lot_parents:
        for seal in p.seals:
            if seal.form == SEAL_WHOLE and (
                "备" in seal.text or "留存" in seal.text or "备用" in seal.text
            ):
                return p
    # 退回：整件封签且未检测的第一件
    whole = [p for p in lot_parents if any(s.form == SEAL_WHOLE for s in p.seals)
             and p.is_intact()]
    return whole[0] if whole else None


def _build_repeat(
    lot: str,
    parent: ParentView,
    usable_neighbor: bool,
    c_state: str,
    c_non_adjacent: bool,
    orders: list[OrderRequirement],
) -> dict[str, Any]:
    fresh_rule = next((o.fresh_set for o in orders if o.fresh_set and o.fresh_set.lot == lot), None)
    if usable_neighbor:
        return {
            "lot": lot,
            "triggered": True,
            "control_source": f"{parent.id} 同母件未用邻片 b",
            "decision": "可用预留件与原对照面配对复测，预留仅进入准备状态",
            "need_fresh_set": False,
            "refs": ["O3", "L4", *parent.events],
        }
    issues = [
        _issue(
            "CONTROL_LOST",
            f"{parent.id} 的 a 已毁损；同母件邻片 b 已用、余片 c "
            + ("已拆封且与透湿位不相邻" if c_state == PIECE_OPENED else "不可作对照"),
            ("H5", "H8", "L4"),
        ),
        _issue("REWITNESS", "从别箱凑齐数量没有意义，须重新见证成组取样", ("L4", "O5")),
    ]
    return {
        "lot": lot,
        "triggered": True,
        "control_source": None,
        "decision": "合并判定缺样，请求重新见证并取得一套新样件",
        "need_fresh_set": True,
        "fresh_set_rule_ref": fresh_rule.refs if fresh_rule else None,
        "visual_still_requested": fresh_rule.visual_still_requested if fresh_rule else None,
        "issues": [i.to_dict() for i in issues],
        "refs": ["O3", "O5", "L4", *parent.events],
    }


def _build_reserve(
    lot_parents: list[ParentView],
    orders: list[OrderRequirement],
    repeat_triggered: bool,
    triggered_parents: set[str],
) -> ReserveStatus:
    reserve_parent = _find_reserve_parent(lot_parents)
    o3 = next(o for o in orders if o.id == "O3")
    lot_triggered = bool(triggered_parents)
    if reserve_parent is None:
        return ReserveStatus(
            parent=None, seal=None, whole_sealed=False,
            triggered=lot_triggered,
            trigger_events=o3.reserve.trigger_events,
            non_triggers=o3.reserve.non_triggers,
            armed=False, paired_control_available=None,
            decision=MISSING,
            detail="无整件原封预留样（工厂转述的“留一片”不满足整件封签）",
            refs=("O1", "O3", "L2"),
        )
    whole_seal = next(s for s in reserve_parent.seals if s.form == SEAL_WHOLE)
    opened = not reserve_parent.is_intact()
    paired = None
    decision = "预留保持原封"
    if lot_triggered:
        # 配对能力由 repeat 块判定；此处仅表达预留件本身进入准备状态
        paired = False
        decision = "复测已触发：预留仅进入准备状态，未自动开封；能否动用取决于同源对照"
    if opened:
        decision = "预留已开封，不得再作完好备件"
    return ReserveStatus(
        parent=reserve_parent.id,
        seal=whole_seal.seal,
        whole_sealed=True,
        triggered=lot_triggered,
        trigger_events=o3.reserve.trigger_events,
        non_triggers=o3.reserve.non_triggers,
        armed=lot_triggered and not opened,
        paired_control_available=paired,
        decision=decision,
        detail=reserve_parent.id + " 整件留存",
        refs=("O1", "O3", *reserve_parent.events),
    )


def _evaluate_visual(
    plan: LotPlan,
    lot_parents: list[ParentView],
    orders: list[OrderRequirement],
    lab: LabRuleSet,
) -> None:
    visual = None
    for p in lot_parents:
        for seal in p.seals:
            if seal.form == SEAL_WHOLE and "外观" in seal.text:
                visual = (p, seal)
                break
    if visual is None:
        # 任何已裁切母件都不能代交外观（L5）；最终缺件在候选分配阶段判定
        return
    p, seal = visual
    ok, rule_ref = _surface_allowed(orders, plan.lot, p.surface)
    issues = []
    if not ok:
        issues.append(_issue("SURFACE_NONCONFORM", f"{p.id} 表面 {p.surface} 禁用", (rule_ref,)))
    plan.allocations.append(Allocation(
        kind=KEEP if ok and not issues else REDRAW,
        role=VISUAL,
        parent=p.id,
        carton=p.carton,
        surface=p.surface,
        pieces=(),
        seal_form=SEAL_WHOLE,
        seal=seal.seal,
        detail="整件外观，未开封，外观收样后须有新指令方可开封",
        issues=tuple(issues),
        refs=(*seal.refs, "L2"),
    ))


def _propose_allocations(
    plan: LotPlan,
    lot: str,
    candidates: list[ParentView],
    orders: list[OrderRequirement],
    lab: LabRuleSet,
) -> None:
    """对尚未落实的用途提出可实际封样的候选分配。"""
    existing_roles = {a.role for a in plan.allocations}
    cartons_used = {a.carton for a in plan.allocations if a.carton}
    mv_piece = lab.piece_for_role(MV) or "a"
    peel_piece = lab.piece_for_role(PEEL) or "b"

    def take() -> Optional[ParentView]:
        for p in candidates:
            if p.carton in cartons_used:
                continue
            cartons_used.add(p.carton)
            return p
        return None

    if MV not in existing_roles or PEEL not in existing_roles:
        shared = take()
        if shared:
            pieces = tuple(x for x in (mv_piece, peel_piece) if x)
            plan.allocations.append(Allocation(
                kind=PROPOSE,
                role=MV,
                parent=shared.id,
                carton=shared.carton,
                surface=shared.surface,
                pieces=(mv_piece,),
                seal_form=SEAL_PIECES,
                seal=None,
                detail="建议与剥离共用同一母件，先完整见证再裁切 a 片",
                refs=("O1", "L1", "L5"),
            ))
            plan.allocations.append(Allocation(
                kind=PROPOSE,
                role=PEEL,
                parent=shared.id,
                carton=shared.carton,
                surface=shared.surface,
                pieces=(peel_piece,),
                seal_form=SEAL_PIECES,
                seal=None,
                detail=f"同母件相邻 {peel_piece} 片，独立试样、不得与 MV 同片",
                refs=("O1", "L1"),
            ))
        else:
            plan.missing.append(_issue(
                "SHORT_CARTONS", "无分箱可用的合规完整件承担透湿+剥离同源取样", ("O1",),
            ))

    if VISUAL not in existing_roles:
        visual = take()
        if visual:
            plan.allocations.append(Allocation(
                kind=PROPOSE,
                role=VISUAL,
                parent=visual.id,
                carton=visual.carton,
                surface=visual.surface,
                pieces=(),
                seal_form=SEAL_WHOLE,
                seal=None,
                detail="建议整件封签做裁切前外观，不得用已裁切母件代交",
                refs=("O1", "L2", "L5"),
            ))
        else:
            plan.missing.append(_issue("SHORT_VISUAL", "无合规完整件承担整件外观", ("O1", "L5")))

    if plan.reserve and plan.reserve.parent is None:
        reserve = take()
        if reserve:
            plan.allocations.append(Allocation(
                kind=PROPOSE,
                role=RESERVE,
                parent=reserve.id,
                carton=reserve.carton,
                surface=reserve.surface,
                pieces=(),
                seal_form=SEAL_WHOLE,
                seal=None,
                detail="建议首次取样即整件另封预留，仅在 O3 触发事件时动用",
                refs=("O1", "O3", "L2"),
            ))

    # 成套复测（对照不可恢复）
    if plan.repeat and plan.repeat.get("need_fresh_set"):
        fresh = take()
        if fresh:
            plan.allocations.append(Allocation(
                kind=PROPOSE,
                role="repeat_set",
                parent=fresh.id,
                carton=fresh.carton,
                surface=fresh.surface,
                pieces=(mv_piece, peel_piece),
                seal_form=SEAL_PIECES,
                seal=None,
                detail="重新见证的成套样件：同源母件、独立相邻小片；预留件不拆来配对",
                refs=("O5", "L4"),
            ))
        else:
            plan.missing.append(_issue(
                "REWITNESS", "无可用合规完整件组成新套样，须安排重新见证", ("O5", "L4"),
            ))


def _collect_missing(
    plan: LotPlan,
    lot: str,
    orders: list[OrderRequirement],
    lab: LabRuleSet,
) -> None:
    # 预留不合规（非整件/已开封）单列
    if plan.reserve and not plan.reserve.whole_sealed:
        plan.missing.append(_issue(
            "OPENED_RESERVE", plan.reserve.detail, plan.reserve.refs,
        ))
    # 去重
    seen: set[str] = set()
    deduped: list[Issue] = []
    for issue in plan.missing:
        key = issue.code + issue.detail
        if key not in seen:
            seen.add(key)
            deduped.append(issue)
    plan.missing = deduped
