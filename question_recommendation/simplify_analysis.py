"""为 simplify 恢复场景生成可删除约束分析。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .models import DeviceCondition, RecommendationContext
from .refusal_rules import SIMPLIFY


REMOVABLE_AGGREGATIONS = {"avg", "min", "max", "sum"}


@dataclass(frozen=True)
class RemovableConstraint:
    """内部可删除约束，最终仅以紧凑 dict 形式传给 LLM。"""

    type: str
    value: str
    id_type: str = ""
    match_mode: str = ""
    device_type: str = ""
    alarm_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为去掉空字段的 Prompt 输入。"""
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in ("", None, [], {})
        }


def analyze_simplify_constraints(
    context: RecommendationContext,
) -> Dict[str, List[Dict[str, Any]]]:
    """返回 simplify 场景中允许 LLM 删除的结构化约束。"""
    if context.recovery_strategy != SIMPLIFY:
        return _empty_analysis()

    constraints: List[RemovableConstraint] = []
    _append_subnet_constraint(constraints, context)
    _append_time_constraint(constraints, context)
    _append_device_locator_constraints(constraints, context)
    _append_alarm_constraint(constraints, context)
    _append_aggregation_constraints(constraints, context)
    _append_extra_kpi_constraints(constraints, context)
    return {"removable_constraints": [item.to_dict() for item in constraints]}


def _append_subnet_constraint(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将子网范围加入可删除约束。"""
    if not context.subnet:
        return
    value = _subnet_value(context)
    if value:
        constraints.append(RemovableConstraint(type="subnet", value=value))


def _append_time_constraint(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将时间范围加入可删除约束。"""
    if context.time:
        constraints.append(RemovableConstraint(type="time", value=context.time))


def _append_device_locator_constraints(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将有效设备定位值加入可删除约束，设备类型本身不加入。"""
    for device in context.devices:
        if not device.device_id:
            continue
        constraints.append(_device_locator_constraint(device))


def _append_alarm_constraint(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将告警过滤条件加入可删除约束。"""
    if not context.alarm:
        return
    value = context.alarm.alarm_value or context.alarm.alarm_type
    if not value:
        return
    constraints.append(
        RemovableConstraint(
            type="alarm",
            value=value,
            alarm_type=context.alarm.alarm_type,
        )
    )


def _append_aggregation_constraints(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将可删除的数值聚合算子加入约束，排除 count/count_distinct/top_n。"""
    for aggregation in context.aggregations:
        normalized = str(aggregation or "").strip().lower()
        if normalized in REMOVABLE_AGGREGATIONS:
            constraints.append(RemovableConstraint(type="aggregation", value=aggregation))


def _append_extra_kpi_constraints(
    constraints: List[RemovableConstraint],
    context: RecommendationContext,
) -> None:
    """将第二个及之后的 KPI 加入可删除约束，单个 KPI 不加入。"""
    for kpi in context.kpis[1:]:
        constraints.append(RemovableConstraint(type="extra_kpi", value=kpi))


def _device_locator_constraint(device: DeviceCondition) -> RemovableConstraint:
    """将设备条件转换为保留上下文的定位约束。"""
    return RemovableConstraint(
        type="device_locator",
        value=device.device_id,
        id_type=device.id_type,
        match_mode=device.match_mode,
        device_type=device.device_type,
    )


def _subnet_value(context: RecommendationContext) -> str:
    """将子网 path/name 合成为面向 Prompt 的范围值。"""
    if not context.subnet:
        return ""
    path = context.subnet.path
    name = context.subnet.name
    if path and name and name not in path:
        return f"{path}/{name}"
    return path or name


def _empty_analysis() -> Dict[str, List[Dict[str, Any]]]:
    """返回稳定的空 simplify 分析结构。"""
    return {"removable_constraints": []}
