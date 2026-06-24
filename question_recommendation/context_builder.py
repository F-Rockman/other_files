"""将上一步意图识别结果和结构化拒答信息转换为最小推荐上下文。"""

import json
from typing import Any, List, Mapping, Optional, Tuple

from query_errors import ErrorInfo

from .models import AlarmCondition, DeviceCondition, RecommendationContext, SubnetScope
from .refusal_rules import (
    ALL_DEVICE_IDENTIFIERS,
    ALL_KPIS,
    BASIC,
    IP_IDENTIFIERS,
    NAME_IDENTIFIERS,
    get_refusal_recovery_rule,
)


AGGREGATION_ALIASES = {
    "count(distinct)": "count_distinct",
    "count_distinct": "count_distinct",
    "topn": "top_n",
    "top_n": "top_n",
}


def build_recommendation_context(
    upstream_result: Mapping[str, Any],
    refuse_info: Optional[ErrorInfo] = None,
    llm_refuse_message: str = "",
) -> RecommendationContext:
    """
    将上一步意图和共享 ErrorInfo 转换为最小推荐上下文。

    恢复策略只由稳定的 ``refuse_info.key`` 决定。详细 LLM 拒答原因仅透传给最终
    Prompt，不参与分类或无效值提取。
    """
    if refuse_info is not None and not isinstance(refuse_info, ErrorInfo):
        raise TypeError("refuse_info 必须是 query_errors.ErrorInfo 或 None")
    if not isinstance(llm_refuse_message, str):
        raise TypeError("llm_refuse_message 必须是字符串")

    data = upstream_result if isinstance(upstream_result, Mapping) else {}
    devices = _mapping_list(data.get("devices"))
    subcomponents = _flatten_subcomponents(data.get("subcomponents"))
    rule = get_refusal_recovery_rule(refuse_info.key) if refuse_info else None
    recovery_strategy = rule.strategy if rule else (BASIC if llm_refuse_message else "")
    all_kpis = _merged_fields(data, devices, subcomponents, "kpis")
    all_properties = _merged_fields(data, devices, subcomponents, "properties")
    invalid_values, invalid_kpis = _resolve_invalid_values(
        rule.invalidation if rule else "",
        devices,
        all_kpis,
    )

    device_conditions = []
    for item in devices:
        condition = _device_condition_from_upstream(item)
        if condition.device_id in invalid_values:
            condition = DeviceCondition(device_type=condition.device_type)
        if condition.device_id or condition.device_type:
            device_conditions.append(condition)

    time_value = data.get("time")
    time_text = (
        json.dumps(time_value, ensure_ascii=False)
        if isinstance(time_value, (Mapping, list))
        else str(time_value or "").strip()
    )
    kpis = [item for item in all_kpis if item not in invalid_kpis]

    return RecommendationContext(
        intention=str(data.get("intention", "") or "").strip(),
        question=str(data.get("question", "") or "").strip(),
        devices=device_conditions,
        subcomponent_types=_dedupe(item.get("subcomponent_type") for item in subcomponents),
        subnet=SubnetScope.from_dict(data.get("subnet")),
        properties=all_properties,
        kpis=kpis,
        time=time_text,
        alarm=AlarmCondition.from_dict(data.get("alarm")),
        aggregations=_normalize_aggregations(data.get("agg")),
        tables=_string_list(data.get("tables")),
        recovery_strategy=recovery_strategy,
        refusal_message=refuse_info.message if refuse_info else "",
        refusal_detail=llm_refuse_message,
        invalid_values=_dedupe(invalid_values + invalid_kpis),
    )


def _resolve_invalid_values(
    invalidation: str,
    devices: List[Mapping[str, Any]],
    kpis: List[str],
) -> Tuple[List[str], List[str]]:
    """根据结构化失效规则，从意图设备标识和 KPI 中确定无效值。"""
    invalid_identifiers: List[str] = []
    invalid_kpis: List[str] = []
    if invalidation == ALL_DEVICE_IDENTIFIERS:
        invalid_identifiers = _dedupe(_device_identifier(item) for item in devices)
    elif invalidation in {IP_IDENTIFIERS, NAME_IDENTIFIERS}:
        expected_type = "IP" if invalidation == IP_IDENTIFIERS else "NAME"
        invalid_identifiers = _dedupe(
            _device_identifier(item)
            for item in devices
            if str(item.get("id_type", "") or "").upper() == expected_type
        )
    elif invalidation == ALL_KPIS:
        invalid_kpis = list(kpis)
    return invalid_identifiers, invalid_kpis


def _normalize_aggregations(value: Any) -> List[str]:
    """将聚合算子规范为小写内部值，并处理 ``topN`` 等别名。"""
    result = []
    for item in _string_list(value):
        normalized = AGGREGATION_ALIASES.get(item.lower(), item.lower())
        if normalized not in result:
            result.append(normalized)
    return result


def _mapping_list(value: Any) -> List[Mapping[str, Any]]:
    """将单个字典或字典列表规范为只包含映射对象的列表。"""
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _flatten_subcomponents(value: Any) -> List[Mapping[str, Any]]:
    """递归展开上游子部件结构，保留父子部件在原文中的先后顺序。"""
    result: List[Mapping[str, Any]] = []
    for item in _mapping_list(value):
        result.append(item)
        result.extend(_flatten_subcomponents(item.get("subcomponents")))
    return result


def _device_condition_from_upstream(item: Mapping[str, Any]) -> DeviceCondition:
    """把上游新字段 ``id`` 映射到当前内部 ``device_id``。"""
    normalized = dict(item)
    if "device_id" not in normalized and "id" in normalized:
        normalized["device_id"] = normalized.get("id")
    return DeviceCondition.from_dict(normalized)


def _device_identifier(item: Mapping[str, Any]) -> Any:
    """读取设备定位值，优先兼容旧字段，其次读取新结构 ``id``。"""
    return item.get("device_id") if "device_id" in item else item.get("id")


def _merged_fields(
    data: Mapping[str, Any],
    devices: List[Mapping[str, Any]],
    subcomponents: List[Mapping[str, Any]],
    field_name: str,
) -> List[str]:
    """合并顶层、设备级和子部件级字段，保持当前扁平筛选逻辑。"""
    values: List[str] = []
    values.extend(_string_list(data.get(field_name)))
    for item in devices:
        values.extend(_string_list(item.get(field_name)))
    for item in subcomponents:
        values.extend(_string_list(item.get(field_name)))
    return _dedupe(values)


def _string_list(value: Any) -> List[str]:
    """将单值或集合规范为去重字符串列表。"""
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return _dedupe(values)


def _dedupe(values: Any) -> List[str]:
    """按输入顺序去重，并忽略空值。"""
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
