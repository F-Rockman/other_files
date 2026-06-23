"""为 simplify 恢复场景生成可删除约束分析。"""

from typing import Any, Dict, List

from .models import DeviceCondition, RecommendationContext
from .refusal_rules import SIMPLIFY


REMOVABLE_AGGREGATIONS = {"avg", "min", "max", "sum"}


def analyze_simplify_constraints(
    context: RecommendationContext,
) -> Dict[str, List[Dict[str, Any]]]:
    """返回 simplify 场景中允许 LLM 删除的结构化约束。"""
    if context.recovery_strategy != SIMPLIFY:
        return _empty_analysis()

    constraints: List[Dict[str, Any]] = []
    _append_subnet_constraint(constraints, context)
    _append_time_constraint(constraints, context)
    _append_device_locator_constraints(constraints, context)
    _append_alarm_constraint(constraints, context)
    _append_aggregation_constraints(constraints, context)
    _append_extra_kpi_constraints(constraints, context)
    return {"removable_constraints": constraints}


def _append_subnet_constraint(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将子网范围加入可删除约束。"""
    if not context.subnet:
        return
    value = _subnet_value(context)
    if value:
        constraints.append({"type": "subnet", "value": value, "role": "range"})


def _append_time_constraint(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将时间范围加入可删除约束。"""
    if context.time:
        constraints.append(
            {"type": "time", "value": context.time, "role": "time_range"}
        )


def _append_device_locator_constraints(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将有效设备定位值加入可删除约束，设备类型本身不加入。"""
    for device in context.devices:
        if not device.device_id:
            continue
        constraints.append(_device_locator_constraint(device))


def _append_alarm_constraint(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将告警过滤条件加入可删除约束。"""
    if not context.alarm:
        return
    value = context.alarm.alarm_value or context.alarm.alarm_type
    if not value:
        return
    constraints.append(
        {
            "type": "alarm",
            "value": value,
            "role": "filter",
            "alarm_type": context.alarm.alarm_type,
        }
    )


def _append_aggregation_constraints(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将可删除的数值聚合算子加入约束，排除 count/count_distinct/top_n。"""
    for aggregation in context.aggregations:
        normalized = str(aggregation or "").strip().lower()
        if normalized in REMOVABLE_AGGREGATIONS:
            constraints.append(
                {"type": "aggregation", "value": aggregation, "role": "aggregation"}
            )


def _append_extra_kpi_constraints(
    constraints: List[Dict[str, Any]],
    context: RecommendationContext,
) -> None:
    """将第二个及之后的 KPI 加入可删除约束，单个 KPI 不加入。"""
    for kpi in context.kpis[1:]:
        constraints.append({"type": "extra_kpi", "value": kpi, "role": "extra_kpi"})


def _device_locator_constraint(device: DeviceCondition) -> Dict[str, Any]:
    """将设备条件转换为保留上下文的定位约束。"""
    constraint = {
        "type": "device_locator",
        "value": device.device_id,
        "role": "locator",
    }
    if device.id_type:
        constraint["id_type"] = device.id_type
    if device.match_mode:
        constraint["match_mode"] = device.match_mode
    if device.device_type:
        constraint["device_type"] = device.device_type
    return constraint


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
