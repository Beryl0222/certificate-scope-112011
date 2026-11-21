"""取样交接工具的领域规则测试。

数据装载共享一套夹具，断言覆盖：指令双语解读、原件/转述冲突、实验室封签与邻接、
实物状态重建、J17 外层澄清影响、J18 复测缺样合并、独立收件与溯源串联。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from corpus import read_collection
from sampling.instructions import (
    parse_orders,
    classify_trigger,
    TRIGGER_MV_NO_READING,
    TRIGGER_SEAL_BREAK,
    NONTRIGGER_LOW_VALID,
    NONTRIGGER_VALID_FAIL,
)
from sampling.labrules import parse_lab_rules
from sampling.reconcile import build_conflicts, Store
from sampling.state import build_state
from sampling.planner import (
    build_lot_plan,
    KEEP,
    REDRAW,
    INVALIDATED,
    PROPOSE,
    MISSING,
)
from sampling.inspection import receive_inspection, ACCEPT, REJECT, PARTIAL
from sampling.trace import trace_ref
from sampling.model import (
    MV,
    PEEL,
    VISUAL,
    RESERVE,
    PIECE_AVAILABLE,
    PIECE_CONSUMED,
    PIECE_CUT,
    PIECE_OPENED,
    PIECE_USED_TESTED,
    SEAL_WHOLE,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


class FixtureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = read_collection(DATA / "inventory.json")
        cls.handover = read_collection(DATA / "handover.json")
        cls.lab_raw = read_collection(DATA / "lab.json")
        cls.orders_raw = read_collection(DATA / "orders.json")
        cls.orders = parse_orders(cls.orders_raw)
        cls.lab = parse_lab_rules(cls.lab_raw)
        cls.parents = build_state(cls.inventory, cls.handover)
        cls.conflicts = build_conflicts(cls.orders)
        cls.surface_rules = {o.surface.lot: o.surface for o in cls.orders if o.surface}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "c.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def confirm_all(self):
        for c in self.conflicts:
            self.store.confirm(c.id, "测试见证员")

    def plan(self, lot):
        return build_lot_plan(
            lot, self.orders, self.lab, self.parents,
            self.conflicts, self.store, self.handover,
        )


class InstructionTests(FixtureTestCase):
    def test_quantity_target_and_unit_separated_for_buyer_and_factory(self):
        o1 = next(o for o in self.orders if o.id == "O1")
        o2 = next(o for o in self.orders if o.id == "O2")
        self.assertEqual(o1.quantity.count, 3)
        self.assertTrue(o1.quantity.per_lot)
        self.assertTrue(o1.quantity.separate_cartons)
        self.assertIn("each lot", o1.quantity.unit_text)
        self.assertEqual(o2.quantity.count, 3)
        self.assertFalse(o2.quantity.per_lot)
        self.assertIn("包装规格", o2.quantity.unit_text)

    def test_shared_parent_vs_independent_panels(self):
        o1 = next(o for o in self.orders if o.id == "O1")
        o2 = next(o for o in self.orders if o.id == "O2")
        binding1 = o1.bindings[0]
        binding2 = o2.bindings[0]
        self.assertTrue(binding1.shared_parent)
        self.assertFalse(binding1.same_piece_allowed)
        self.assertFalse(binding2.shared_parent)

    def test_reserve_is_whole_panel_sealed_first_time(self):
        o1 = next(o for o in self.orders if o.id == "O1")
        o2 = next(o for o in self.orders if o.id == "O2")
        self.assertTrue(o1.reserve.whole_panel_sealed)
        self.assertFalse(o2.reserve.whole_panel_sealed)

    def test_repeat_triggers_and_non_triggers(self):
        o3 = next(o for o in self.orders if o.id == "O3")
        self.assertIn(TRIGGER_SEAL_BREAK, o3.reserve.trigger_events)
        self.assertIn(TRIGGER_MV_NO_READING, o3.reserve.trigger_events)
        self.assertIn(NONTRIGGER_LOW_VALID, o3.reserve.non_triggers)
        self.assertTrue(o3.reserve.paired_with_original)

    def test_event_classification_matches_buyer_documents(self):
        kind, trigger = classify_trigger("透湿读数过低，有完整读数，a 已消耗。")
        self.assertEqual(kind, NONTRIGGER_LOW_VALID)
        self.assertFalse(trigger)
        kind, trigger = classify_trigger("a 无读数，b 已从原母件用去。")
        self.assertEqual(kind, TRIGGER_MV_NO_READING)
        self.assertTrue(trigger)
        kind, trigger = classify_trigger("残片经过拆包，剩余 c 未与透湿位相邻。")
        self.assertEqual(kind, TRIGGER_SEAL_BREAK)
        self.assertTrue(trigger)
        kind, trigger = classify_trigger("已测不合格但读数有效。")
        self.assertEqual(kind, NONTRIGGER_VALID_FAIL)
        self.assertFalse(trigger)

    def test_surface_and_fresh_set_rules_are_lot_scoped(self):
        o4 = next(o for o in self.orders if o.id == "O4")
        o5 = next(o for o in self.orders if o.id == "O5")
        self.assertEqual(o4.surface.lot, "J17")
        self.assertEqual(o4.surface.allowed, "朝开口")
        self.assertIn("朝内壁", o4.surface.banned)
        self.assertEqual(o5.fresh_set.lot, "J18")
        self.assertTrue(o5.fresh_set.fresh_set)
        self.assertTrue(o5.fresh_set.visual_still_requested)


class ConflictTests(FixtureTestCase):
    def test_three_sampling_changing_conflicts(self):
        ids = {c.id for c in self.conflicts}
        self.assertEqual(ids, {"C1", "C2", "C3"})
        topics = " ".join(c.topic for c in self.conflicts)
        self.assertIn("取样单位", topics)
        self.assertIn("共享同一来源", topics)
        self.assertIn("留样形态", topics)

    def test_plan_is_draft_until_witness_confirms(self):
        fresh_store = Store(Path(self.tmp.name) / "draft.json")
        draft = build_lot_plan(
            "J17", self.orders, self.lab, self.parents,
            self.conflicts, fresh_store, self.handover,
        )
        self.assertTrue(draft.to_dict()["draft_only"])
        self.assertEqual({c["id"] for c in draft.conflicts_blocking}, {"C1", "C2", "C3"})
        self.confirm_all()
        self.assertFalse(self.plan("J17").to_dict()["draft_only"])

    def test_confirmation_persists_across_store_instances(self):
        self.store.confirm("C2", "李见证", note="同源母件")
        reopened = Store(self.store.path)
        self.assertTrue(reopened.is_confirmed("C2"))
        self.assertEqual(reopened.get("C2")["witness"], "李见证")
        self.assertFalse(reopened.is_confirmed("C3"))
        with self.assertRaises(KeyError):
            self.store.confirm("CX", "李见证")


class LabRuleTests(FixtureTestCase):
    def test_role_pieces_and_adjacency(self):
        self.assertEqual(self.lab.piece_for_role(MV), "a")
        self.assertEqual(self.lab.piece_for_role(PEEL), "b")
        self.assertTrue(self.lab.is_adjacent("a", "b"))
        self.assertFalse(self.lab.is_adjacent("a", "c"))

    def test_mv_consumes_and_whole_seal_forms(self):
        self.assertTrue(self.lab.role_consumes(MV))
        self.assertFalse(self.lab.role_consumes(PEEL))
        self.assertTrue(self.lab.accepts_seal_form(SEAL_WHOLE))
        self.assertTrue(self.lab.intact_witness_required())
        self.assertTrue(self.lab.repeat_control_same_parent_only())
        self.assertTrue(self.lab.duplicate_slip_required())


class StateTests(FixtureTestCase):
    def test_piece_states_reflect_destructive_history(self):
        p2 = self.parents["P2"]
        self.assertEqual(p2.pieces["a"].state, PIECE_CONSUMED)
        self.assertEqual(p2.pieces["b"].state, PIECE_CUT)
        self.assertEqual(p2.pieces["c"].state, PIECE_AVAILABLE)
        p6 = self.parents["P6"]
        self.assertEqual(p6.pieces["a"].state, PIECE_CONSUMED)
        self.assertEqual(p6.pieces["b"].state, PIECE_USED_TESTED)
        self.assertEqual(p6.pieces["c"].state, PIECE_OPENED)

    def test_consumed_or_opened_pieces_cannot_be_reallocated(self):
        for pid in ("P2", "P6"):
            self.assertEqual(self.parents[pid].usable_pieces(),
                             [n for n, v in self.parents[pid].pieces.items()
                              if v.state == PIECE_AVAILABLE])
        self.assertNotIn("a", self.parents["P2"].usable_pieces())
        self.assertNotIn("c", self.parents["P6"].usable_pieces())

    def test_inferred_seal_for_tested_parent_without_seal_action(self):
        s03 = next(s for s in self.parents["P6"].seals if s.seal == "S03")
        self.assertTrue(s03.inferred)
        self.assertEqual(set(s03.pieces), {"a", "b"})

    def test_intact_witness_gate_detects_missing_witness(self):
        self.assertFalse(self.parents["P2"].intact_witness_before_cut)
        self.assertTrue(self.parents["P6"].intact_witness_before_cut)

    def test_reserve_and_visual_parents_stay_intact(self):
        self.assertTrue(self.parents["P3"].is_intact())
        self.assertTrue(self.parents["P7"].is_intact())
        self.assertTrue(self.parents["P8"].is_intact())


class J17PlanTests(FixtureTestCase):
    def setUp(self):
        self.confirm_all()
        self.plan = self.plan("J17")

    def test_valid_low_result_does_not_trigger_repeat(self):
        self.assertIsNone(self.plan.repeat)
        self.assertFalse(self.plan.reserve.triggered)
        self.assertFalse(self.plan.reserve.armed)

    def test_reserve_panel_kept_whole_and_sealed(self):
        self.assertEqual(self.plan.reserve.parent, "P3")
        self.assertEqual(self.plan.reserve.seal, "S02")
        self.assertTrue(self.plan.reserve.whole_sealed)

    def test_outer_surface_clarification_invalidates_tested_inner_panel(self):
        impact = {i["parent"]: i["decision"] for i in self.plan.clarification_impact}
        self.assertEqual(impact["P2"], INVALIDATED)
        self.assertEqual(impact["P3"], KEEP)
        self.assertEqual(impact["P1"], KEEP)
        # 已测不合格（低值但有效）的结果对本次委托失效
        mv = next(a for a in self.plan.allocations if a.role == MV and a.parent == "P2")
        self.assertEqual(mv.kind, INVALIDATED)
        codes = {i.code for i in mv.issues}
        self.assertIn("SURFACE_NONCONFORM", codes)
        self.assertIn("NO_INTACT_WITNESS", codes)

    def test_redraw_uses_only_outer_intact_separate_carton_parents(self):
        proposed = [a for a in self.plan.allocations if a.kind == PROPOSE]
        parents_used = {a.parent for a in proposed}
        self.assertTrue(parents_used)
        for pid in parents_used:
            p = self.parents[pid]
            self.assertEqual(p.surface, "朝开口")
            self.assertTrue(p.is_intact())
        # 同源：MV 与剥离候选落在同一母件
        mv = next(a for a in proposed if a.role == MV)
        peel = next(a for a in proposed if a.role == PEEL)
        self.assertEqual(mv.parent, peel.parent)
        self.assertNotEqual(mv.pieces, peel.pieces)
        # 分箱：不同候选母件来自不同箱（同一母件的 MV/剥离两行不算重复）
        cartons = list({a.parent: a.carton for a in proposed}.values())
        self.assertEqual(len(cartons), len(set(cartons)))

    def test_no_missing_when_candidates_exist(self):
        self.assertEqual(self.plan.missing, [])

    def test_missing_visual_when_no_intact_panel_available(self):
        # 构造极端情形：把候选完整件全部标记消耗后应报缺外观
        from sampling.model import PIECE_CONSUMED
        from sampling.state import ParentView
        parents = {pid: self._clone(p) for pid, p in self.parents.items()}
        for pid in ("P1", "P4", "P5"):
            for name in parents[pid].pieces:
                parents[pid].pieces[name] = type(parents[pid].pieces[name])(
                    name, PIECE_CONSUMED, ("X",)
                )
        plan = build_lot_plan(
            "J17", self.orders, self.lab, parents,
            self.conflicts, self.store, self.handover,
        )
        self.assertTrue(any(i.code == "SHORT_VISUAL" for i in plan.missing))

    def test_sealed_inner_facing_panel_without_test_must_be_unsealed_and_redrawn(self):
        # 构造：J17 一件内层表面、已整件封签但未检测 → 应判解封重取而非失效
        from dataclasses import replace
        from sampling.state import SealView
        parents = {pid: self._clone(p) for pid, p in self.parents.items()}
        target = parents["P10"]
        target = replace(target, lot="J17")
        target.seals.append(SealView(
            seal="S88", parent="P10", form=SEAL_WHOLE, pieces=(),
            lab_hint="西实验室", inferred=False, text="整件留存", refs=("HZ",),
        ))
        parents["P10"] = target
        plan = build_lot_plan(
            "J17", self.orders, self.lab, parents,
            self.conflicts, self.store, self.handover,
        )
        entry = next(i for i in plan.clarification_impact if i["parent"] == "P10")
        self.assertEqual(entry["decision"], REDRAW)
        self.assertNotEqual(entry["decision"], INVALIDATED)

    @staticmethod
    def _clone(view):
        from dataclasses import replace
        return replace(view, pieces=dict(view.pieces), seals=list(view.seals),
                       events=list(view.events))


class J18PlanTests(FixtureTestCase):
    def setUp(self):
        self.confirm_all()
        self.plan = self.plan("J18")

    def test_no_reading_and_seal_break_trigger_reserve_armed_only(self):
        self.assertTrue(self.plan.reserve.triggered)
        self.assertTrue(self.plan.reserve.armed)
        # 预留件本身没有被开封
        self.assertEqual(self.plan.reserve.parent, "P7")
        self.assertTrue(self.parents["P7"].is_intact())

    def test_lost_control_merges_into_missing_and_requires_rewitness(self):
        repeat = self.plan.repeat
        self.assertTrue(repeat["need_fresh_set"])
        self.assertIsNone(repeat["control_source"])
        codes = {i["code"] for i in repeat["issues"]}
        self.assertIn("CONTROL_LOST", codes)
        self.assertIn("REWITNESS", codes)

    def test_reserve_from_another_carton_cannot_pair_as_control(self):
        # P7 是别箱预留件，不允许被当作 P6 的同源对照
        self.assertNotEqual(self.plan.reserve.parent, "P6")
        fresh_parents = [a.parent for a in self.plan.allocations
                         if a.role == "repeat_set"]
        self.assertNotIn("P7", fresh_parents)
        fresh = next(a for a in self.plan.allocations if a.role == "repeat_set")
        self.assertEqual(fresh.kind, PROPOSE)

    def test_visual_panel_independently_sealed_remains_requested(self):
        visual = next(a for a in self.plan.allocations if a.role == VISUAL)
        self.assertEqual(visual.parent, "P8")
        self.assertEqual(visual.seal_form, SEAL_WHOLE)
        self.assertEqual(visual.kind, KEEP)

    def test_fresh_set_uses_separate_carton_and_independent_adjacent_pieces(self):
        fresh = next(a for a in self.plan.allocations if a.role == "repeat_set")
        self.assertEqual(fresh.parent, "P9")
        self.assertEqual(set(fresh.pieces), {"a", "b"})
        self.assertNotEqual(fresh.carton, self.parents["P6"].carton)

    def test_j18_has_no_surface_restriction(self):
        # O4 仅限定 J17；J18 的朝内壁件 P10 仍是合规候选
        self.assertEqual(self.plan.clarification_impact, [])
        self.assertIn("P10", self.plan.candidate_parents)


class InspectionTests(FixtureTestCase):
    def test_decisions_are_per_seal_and_independent(self):
        decisions = receive_inspection(self.parents, self.lab, self.surface_rules)
        by_seal = {d.seal: d for d in decisions}
        # S01：a 已消耗拒收，b 仍可独立开测 → 部分接收
        self.assertEqual(by_seal["S01"].decision, PARTIAL)
        self.assertEqual(by_seal["S01"].accepted_pieces, ("b",))
        self.assertIn("a", by_seal["S01"].rejected_pieces)
        # S03：两片均已用于检测/消耗，整签拒收
        self.assertEqual(by_seal["S03"].decision, REJECT)
        self.assertEqual(set(by_seal["S03"].rejected_pieces), {"a", "b"})
        # 不受影响的整件签仍可接收
        self.assertEqual(by_seal["S02"].decision, ACCEPT)
        self.assertEqual(by_seal["S04"].decision, ACCEPT)
        self.assertEqual(by_seal["S05"].decision, ACCEPT)

    def test_west_lab_duplicate_slip_gate(self):
        # 构造西实验室 b 片小片签：缺副联拒收，补副联后可收
        from sampling.state import SealView
        from sampling.model import SEAL_PIECES
        parents = {pid: self._clone(p) for pid, p in self.parents.items()}
        parents["P9"].seals.append(SealView(
            seal="S99", parent="P9", form=SEAL_PIECES, pieces=("b",),
            lab_hint="西实验室", inferred=False, text="b 片对照", refs=("HX",),
        ))
        parents["P9"].pieces["b"] = type(parents["P9"].pieces["b"])(
            "b", PIECE_USED_TESTED, ("HX",)
        )
        without = receive_inspection(parents, self.lab, self.surface_rules)
        d = next(x for x in without if x.seal == "S99")
        self.assertIn("DUPLICATE_SLIP", [i.code for i in d.issues])
        with_slip = receive_inspection(
            parents, self.lab, self.surface_rules, frozenset({"S99"})
        )
        d2 = next(x for x in with_slip if x.seal == "S99")
        self.assertNotIn("DUPLICATE_SLIP", [i.code for i in d2.issues])

    @staticmethod
    def _clone(view):
        from dataclasses import replace
        return replace(view, pieces=dict(view.pieces), seals=list(view.seals),
                       events=list(view.events))


class TraceTests(FixtureTestCase):
    def test_trace_by_seal_links_order_evidence_seal_and_test_use(self):
        result = trace_ref("S01", self.orders, self.inventory,
                           self.handover, self.parents)
        self.assertTrue(result["found"])
        kinds = [n["kind"] for n in result["chain"]]
        self.assertIn("指令", kinds)
        self.assertIn("库存身份", kinds)
        self.assertIn("封签（取样证明）", kinds)
        self.assertIn("签收", kinds)
        self.assertIn("检测", kinds)
        refs = [n["ref"] for n in result["chain"]]
        for expected in ("O1", "O2", "O3", "P2", "H1", "H3", "H4"):
            self.assertIn(expected, refs)

    def test_trace_by_parent_and_handover_id(self):
        for query in ("P6", "H8"):
            result = trace_ref(query, self.orders, self.inventory,
                               self.handover, self.parents)
            self.assertTrue(result["found"])
            self.assertEqual(result["lot"], "J18")
            self.assertIn("P6", result["parent_ids"])

    def test_trace_keeps_raw_text_for_every_evidence_step(self):
        result = trace_ref("S03", self.orders, self.inventory,
                           self.handover, self.parents)
        raw_nodes = [n for n in result["chain"] if n["raw"]]
        refs = {n["ref"] for n in raw_nodes}
        # 指令原文与交接记录原文必须逐字保留在链路中
        for expected in ("O1", "O3", "O5", "H5", "H8"):
            self.assertIn(expected, refs)
        h5 = next(n for n in raw_nodes if n["ref"] == "H5")
        self.assertIn("a 无读数", h5["raw"])

    def test_unknown_query(self):
        result = trace_ref("NOPE", self.orders, self.inventory,
                           self.handover, self.parents)
        self.assertFalse(result["found"])


class SerializationTests(FixtureTestCase):
    def test_all_outputs_are_json_serializable(self):
        self.confirm_all()
        for lot in ("J17", "J18"):
            json.dumps(self.plan(lot).to_dict(), ensure_ascii=False)
        json.dumps([o.to_dict() for o in self.orders], ensure_ascii=False)
        json.dumps(self.lab.to_list(), ensure_ascii=False)
        json.dumps(
            [d.to_dict() for d in receive_inspection(
                self.parents, self.lab, self.surface_rules)],
            ensure_ascii=False,
        )
        json.dumps(trace_ref("S03", self.orders, self.inventory,
                             self.handover, self.parents), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
