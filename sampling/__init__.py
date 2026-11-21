"""跨境委托双语见证取样与封样交接工具。

模块划分：

- ``model``         领域数据结构、用途与原因码常量
- ``instructions``  解析买家/工厂/后续指令（数量对象、取样单位、同源共享、独立试样）
- ``labrules``      解析实验室与见证站收样约定
- ``reconcile``     英文原件与工厂转述的冲突比对与见证员确认
- ``state``         由库存与交接记录重建母件/小片/封签的实物状态
- ``planner``       逐批生成可封样、可收样分配、缺件与复测判定
- ``inspection``    检测员按交接单逐项收件/拒收
- ``trace``         指令—取样证明—封签—检测用途串联
- ``store``         见证员确认的持久化
- ``cli``           命令行入口
"""

from sampling.instructions import parse_orders
from sampling.labrules import parse_lab_rules
from sampling.reconcile import Conflict, build_conflicts, Store
from sampling.state import build_state
from sampling.planner import build_lot_plan
from sampling.inspection import receive_inspection
from sampling.trace import trace_ref

__all__ = [
    "parse_orders",
    "parse_lab_rules",
    "Conflict",
    "build_conflicts",
    "Store",
    "build_state",
    "build_lot_plan",
    "receive_inspection",
    "trace_ref",
]
