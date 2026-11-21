"""命令行入口：把资料读取、解析、计划、收件、溯源串成可操作工具。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corpus import read_collection
from sampling.instructions import parse_orders, classify_trigger
from sampling.labrules import parse_lab_rules
from sampling.reconcile import build_conflicts, Store
from sampling.state import build_state
from sampling.planner import build_lot_plan
from sampling.inspection import receive_inspection
from sampling.trace import trace_ref

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_STORE = ROOT / "work" / "confirmations.json"


def _load():
    orders_raw = read_collection(DATA / "orders.json")
    inventory = read_collection(DATA / "inventory.json")
    lab_raw = read_collection(DATA / "lab.json")
    handover = read_collection(DATA / "handover.json")
    orders = parse_orders(orders_raw)
    lab = parse_lab_rules(lab_raw)
    parents = build_state(inventory, handover)
    conflicts = build_conflicts(orders)
    surface_rules = {o.surface.lot: o.surface for o in orders if o.surface}
    return orders_raw, orders, inventory, lab, handover, parents, conflicts, surface_rules


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_instructions(args) -> None:
    _, orders, *_ = _load()
    payload = [o.to_dict() for o in orders]
    if args.id:
        payload = [o.to_dict() for o in orders if o.id == args.id]
    _print(payload, args.json)


def _cmd_conflicts(args) -> None:
    _, orders, *_, conflicts, _ = _load()
    store = Store(args.store)
    if args.confirm:
        record = store.confirm(args.confirm, args.witness, note=args.note or "")
        _print({"confirmed": record}, args.json)
        return
    payload = []
    for c in conflicts:
        item = c.to_dict()
        item["confirmed"] = store.is_confirmed(c.id)
        item["confirmation"] = store.get(c.id)
        payload.append(item)
    _print(payload, args.json)


def _cmd_plan(args) -> None:
    _, orders, _, lab, handover, parents, conflicts, surface_rules = _load()
    store = Store(args.store)
    lots = sorted({p.lot for p in parents.values()})
    if args.lot:
        lots = [args.lot]
    payload = []
    for lot in lots:
        plan = build_lot_plan(lot, orders, lab, parents, conflicts, store, handover)
        payload.append(plan.to_dict())
    _print(payload if len(payload) > 1 else payload[0], args.json)


def _cmd_inspect(args) -> None:
    _, orders, _, lab, _, parents, _, surface_rules = _load()
    slips = frozenset(args.duplicate_slip or [])
    decisions = receive_inspection(parents, lab, surface_rules, slips)
    if args.seal:
        decisions = [d for d in decisions if d.seal == args.seal]
    _print([d.to_dict() for d in decisions], args.json)


def _cmd_trace(args) -> None:
    orders_raw, orders, inventory, _, handover, parents, _, _ = _load()
    result = trace_ref(args.query, orders, inventory, handover, parents)
    _print(result, args.json)


def _cmd_event(args) -> None:
    kind, triggered = classify_trigger(args.text)
    _print(
        {"event": kind, "triggers_repeat": triggered, "text": args.text},
        args.json,
    )


def main(argv: list[str] | None = None) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=str(DEFAULT_STORE), help="见证确认存储路径")
    parser = argparse.ArgumentParser(
        prog="sampling",
        description="跨境委托双语见证取样与封样交接工具",
        parents=[common],
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("instructions", help="查看结构化指令解读", parents=[common])
    p.add_argument("--id", help="只看某条来件")
    p.set_defaults(func=_cmd_instructions)

    p = sub.add_parser("conflicts", help="列出/确认英文原件与工厂转述的冲突", parents=[common])
    p.add_argument("--confirm", choices=["C1", "C2", "C3"], help="确认某条冲突（采用买家原件）")
    p.add_argument("--witness", default="见证员", help="确认见证员姓名")
    p.add_argument("--note", help="确认备注")
    p.set_defaults(func=_cmd_conflicts)

    p = sub.add_parser("plan", help="生成逐批封样/收样分配与缺件清单", parents=[common])
    p.add_argument("--lot", help="只看某批")
    p.set_defaults(func=_cmd_plan)

    p = sub.add_parser("inspect", help="检测员按交接单逐项收件结论", parents=[common])
    p.add_argument("--seal", help="只看某个封签")
    p.add_argument("--duplicate-slip", nargs="*", help="已附见证副联的封签号")
    p.set_defaults(func=_cmd_inspect)

    p = sub.add_parser("trace", help="按封签号/母件号/交接记录号溯源", parents=[common])
    p.add_argument("query")
    p.set_defaults(func=_cmd_trace)

    p = sub.add_parser("event", help="分类一条检测/运输原文是否触发复测", parents=[common])
    p.add_argument("text")
    p.set_defaults(func=_cmd_event)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
