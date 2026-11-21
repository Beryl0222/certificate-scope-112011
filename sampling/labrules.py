"""解析实验室收样约定与见证站规则。

把实验室认可的封签形态、用途-小片对应、邻接关系、整件见证要求结构化。
邻接关系以母件可切片序列上的相邻为准（见 L1：a 与 b 相邻、a 与 c 不相邻）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sampling.model import MV, PEEL, SEAL_WHOLE, SEAL_PIECES

# 规则类型
R_ROLE_PIECE = "用途小片对应"
R_SEAL_FORM = "封签形态"
R_CONSUMES = "破坏性消耗"
R_ADJACENCY = "邻接关系"
R_CUT_GATE = "裁切前完整见证"
R_DUPLICATE_SLIP = "见证副联"
R_REPEAT_CONTROL = "复测对照限制"


@dataclass(frozen=True)
class LabRule:
    id: str
    source: str
    raw: str
    kind: str
    # 用途 -> 小片（如 MV -> a、peel -> b）
    role_piece: Optional[dict[str, str]] = None
    # 认可的封签形态集合
    accepted_seal_forms: Optional[frozenset[str]] = None
    # 邻接小片对（无序）
    adjacent_pairs: Optional[tuple[tuple[str, str], ...]] = None
    consumes: tuple[str, ...] = ()
    requires_intact_witness_before_cut: bool = False
    requires_duplicate_slip: bool = False
    repeat_limited_to_same_parent_unused: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "来源": self.source,
            "kind": self.kind,
            "role_piece": self.role_piece,
            "accepted_seal_forms": (
                sorted(self.accepted_seal_forms) if self.accepted_seal_forms else None
            ),
            "adjacent_pairs": [list(p) for p in self.adjacent_pairs] if self.adjacent_pairs else None,
            "consumes": list(self.consumes),
            "requires_intact_witness_before_cut": self.requires_intact_witness_before_cut,
            "requires_duplicate_slip": self.requires_duplicate_slip,
            "repeat_limited_to_same_parent_unused": self.repeat_limited_to_same_parent_unused,
            "note": self.note,
            "原文": self.raw,
        }


@dataclass(frozen=True)
class LabRuleSet:
    rules: tuple[LabRule, ...]

    def piece_for_role(self, role: str) -> Optional[str]:
        """实验室规定的某用途小片；无规定返回 None。"""
        for rule in self.rules:
            if rule.role_piece and role in rule.role_piece:
                return rule.role_piece[role]
        return None

    def is_adjacent(self, x: str, y: str) -> bool:
        for rule in self.rules:
            if rule.adjacent_pairs and tuple(sorted((x, y))) in rule.adjacent_pairs:
                return True
        return False

    def accepts_seal_form(self, form: str) -> bool:
        forms: set[str] = set()
        for rule in self.rules:
            if rule.accepted_seal_forms:
                forms.update(rule.accepted_seal_forms)
        return form in forms

    def role_consumes(self, role: str) -> bool:
        return any(role in rule.consumes for rule in self.rules)

    def intact_witness_required(self) -> bool:
        return any(r.requires_intact_witness_before_cut for r in self.rules)

    def duplicate_slip_required(self) -> bool:
        return any(r.requires_duplicate_slip for r in self.rules)

    def repeat_control_same_parent_only(self) -> bool:
        return any(r.repeat_limited_to_same_parent_unused for r in self.rules)

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.rules]


def parse_lab_rules(records: list[dict]) -> LabRuleSet:
    rules: list[LabRule] = []
    for row in records:
        rid, source, text = row["id"], row["来源"], row["原文"]
        if rid == "L1":
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_ROLE_PIECE,
                role_piece={MV: "a", PEEL: "b"},
                accepted_seal_forms=frozenset({SEAL_PIECES}),
                note="两片可同母件，不可为同一小片；按 a/b 小片封签收",
            ))
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_CONSUMES,
                consumes=(MV,), note="MV 耗尽试样",
            ))
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_ADJACENCY,
                adjacent_pairs=(("a", "b"),), note="a 与 b 相邻，a 与 c 不相邻",
            ))
        elif rid == "L2":
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_SEAL_FORM,
                accepted_seal_forms=frozenset({SEAL_WHOLE}),
                note="外观和备用件必须按整件封签；开封后不得再作完好备件",
            ))
        elif rid == "L3":
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_SEAL_FORM,
                accepted_seal_forms=frozenset({SEAL_WHOLE, SEAL_PIECES}),
                note="可收整件，接受 a、b、c 相邻的 b 片作对照",
            ))
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_DUPLICATE_SLIP,
                requires_duplicate_slip=True,
                note="另寄原件身份的见证副联",
            ))
        elif rid == "L4":
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_REPEAT_CONTROL,
                repeat_limited_to_same_parent_unused=True,
                note="复测共享对照以前次留在母件上的未用邻片为限，无可用对照即重新见证成组取样",
            ))
        elif rid == "L5":
            rules.append(LabRule(
                id=rid, source=source, raw=text, kind=R_CUT_GATE,
                requires_intact_witness_before_cut=True,
                note="裁切前先做完整状态身份和外观见证；整件外观项不能用已裁切母件代交",
            ))
    return LabRuleSet(rules=tuple(rules))
