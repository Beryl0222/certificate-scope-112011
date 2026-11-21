"""检测员按交接单收件：逐项拒收/接收，能独立开测的仍可开测。

判定依据：实验室只收自己认可的封签形态（L2/L3）、表面限定（O4）、小片实际状态
（已拆封/已消耗不得按余料再分）、完整见证门（L5）、西实验室见证副联（L3）。
每个封签独立出结论，一处不符不牵连其他可独立开测的件。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sampling.labrules import LabRule, LabRuleSet
from sampling.model import (
    PIECE_CONSUMED,
    PIECE_OPENED,
    PIECE_USED_TESTED,
    SEAL_INFERRED,
    SEAL_PIECES,
    SEAL_WHOLE,
    Issue,
)
from sampling.state import ParentView

ACCEPT = "接收"
REJECT = "拒收"
PARTIAL = "部分接收"


@dataclass(frozen=True)
class ReceiptDecision:
    seal: str
    parent: str
    lab: str
    decision: str
    accepted_pieces: tuple[str, ...]
    rejected_pieces: tuple[str, ...]
    detail: str
    issues: tuple[Issue, ...]
    refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seal": self.seal,
            "parent": self.parent,
            "lab": self.lab,
            "decision": self.decision,
            "accepted_pieces": list(self.accepted_pieces),
            "rejected_pieces": list(self.rejected_pieces),
            "detail": self.detail,
            "issues": [i.to_dict() for i in self.issues],
            "ref_events": list(self.refs),
        }


def _surface_ok(lot_surface_rule, surface: str) -> tuple[bool, Optional[str]]:
    if lot_surface_rule is None:
        return True, None
    if surface in lot_surface_rule.banned:
        return False, lot_surface_rule.refs[0]
    return surface == lot_surface_rule.allowed, lot_surface_rule.refs[0]


def _rules_for_lab(lab: LabRuleSet, lab_name: str) -> list[LabRule]:
    """只适用收件实验室自己的约定与见证站统一规则。

    来源不明的封签不套用任何一家实验室的特有约定（如西实验室副联），
    仅按见证站规则与实物状态核对，避免张冠李戴。
    """
    if not lab_name or lab_name == "未指定实验室":
        return [r for r in lab.rules if r.source == "见证站"]
    return [r for r in lab.rules if r.source == lab_name or r.source == "见证站"]


def receive_inspection(
    parents: dict[str, ParentView],
    lab: LabRuleSet,
    surface_rules: dict[str, Any],
    duplicate_slip_present: frozenset[str] = frozenset(),
) -> list[ReceiptDecision]:
    """对每个封签出具独立收件结论。

    ``surface_rules`` 为 ``{批: SurfaceRule}``；``duplicate_slip_present`` 为已附
    见证副联的封签号集合（仅西实验室 b 片对照必需）。
    """
    decisions: list[ReceiptDecision] = []
    for parent in parents.values():
        rule = surface_rules.get(parent.lot)
        for seal in parent.seals:
            issues: list[Issue] = []
            accepted: list[str] = []
            rejected: list[str] = []
            lab_name = seal.lab_hint or "未指定实验室"
            local = _rules_for_lab(lab, lab_name)
            accepted_forms: set[str] = set()
            intact_gate = False
            dup_slip = False
            for r in local:
                if r.accepted_seal_forms:
                    accepted_forms.update(r.accepted_seal_forms)
                if r.requires_intact_witness_before_cut:
                    intact_gate = True
                if r.requires_duplicate_slip:
                    dup_slip = True

            # 封签形态认可：推断签按其实际内容对应的形态核对，推断本身不是拒收理由
            effective_form = SEAL_PIECES if seal.form == SEAL_INFERRED else seal.form
            if accepted_forms and effective_form not in accepted_forms:
                issues.append(Issue(
                    "SEAL_FORM",
                    f"{lab_name}不认可{effective_form}（封签 {seal.seal} 内容按实际推断）",
                    seal.refs,
                ))

            # 表面限定
            ok, ref = _surface_ok(rule, parent.surface)
            if not ok:
                issues.append(Issue(
                    "SURFACE_NONCONFORM",
                    f"{parent.id} 表面 {parent.surface} 违反外层限定",
                    (ref,),
                ))

            # 逐片核对实际状态：已拆封/消耗/已测不得按余料再分
            for name in seal.pieces:
                state = parent.pieces[name].state
                if state in {PIECE_OPENED, PIECE_CONSUMED, PIECE_USED_TESTED}:
                    rejected.append(name)
                    issues.append(Issue(
                        "PIECE_ALREADY_USED",
                        f"片 {name} 状态为{state}，不能再分给他用",
                        seal.refs,
                    ))
                else:
                    accepted.append(name)

            # 整件封签：必须保持完整
            if effective_form == SEAL_WHOLE and not parent.is_intact():
                issues.append(Issue(
                    "OPENED_RESERVE",
                    f"{parent.id} 已非完整件，整件封签内容与实物不符",
                    seal.refs,
                ))

            # 西实验室 b 片对照须见证副联（仅西实验室约定）
            if dup_slip and "b" in seal.pieces:
                if seal.seal not in duplicate_slip_present:
                    issues.append(Issue(
                        "DUPLICATE_SLIP",
                        "缺原件身份见证副联，b 片对照暂不可收",
                        seal.refs,
                    ))

            # 裁切门（见证站统一要求）
            if seal.pieces and intact_gate and not parent.intact_witness_before_cut:
                issues.append(Issue(
                    "NO_INTACT_WITNESS",
                    f"{parent.id} 裁切前无完整状态见证记录",
                    ("L5",),
                ))

            if not issues:
                decision = ACCEPT
                detail = "封签形态、表面、小片状态均符合，可独立开测"
            elif seal.pieces and accepted and not rejected:
                # 小片本身可用但有程序性问题：整签暂拒收
                decision = REJECT
                detail = "程序或封签要求不符，整签拒收"
            elif accepted:
                decision = PARTIAL
                detail = "符合部分可独立开测，不符部分拒收"
            else:
                decision = REJECT
                detail = "与委托/收样要求不符，拒收"

            decisions.append(ReceiptDecision(
                seal=seal.seal,
                parent=parent.id,
                lab=lab_name,
                decision=decision,
                accepted_pieces=tuple(accepted),
                rejected_pieces=tuple(rejected),
                detail=detail,
                issues=tuple(issues),
                refs=seal.refs,
            ))
    return decisions
