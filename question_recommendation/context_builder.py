"""将上一步意图识别结果转换为最小化 RecommendationContext。"""

import json
import re
from typing import Any, List, Mapping

from .models import AlarmCondition, Identifier, RecommendationContext


AGGREGATION_ALIASES = {
    "count(distinct)": "count_distinct",
    "count_distinct": "count_distinct",
    "topn": "top_n",
    "top_n": "top_n",
}

FAILURE_PATTERNS = [
    ("业务域不明确", ("多领域", "多个领域", "跨领域", "领域不明确", "业务域不明确")),
    ("匹配到多设备", ("多设备", "多个设备", "匹配到多个", "匹配到多条")),
    ("指标不支持", ("指标不支持", "不支持该指标", "指标不存在", "未找到指标")),
    ("属性不支持", ("属性不支持", "不支持该属性", "属性不存在", "未找到属性")),
    ("父对象定位失败", ("父对象", "所属设备", "上级设备")),
    ("对象定位失败", ("对象不存在", "设备不存在", "未找到设备", "无法定位", "未匹配到")),
    ("时间缺失", ("缺少时间", "时间缺失", "未指定时间")),
    ("条件过细", ("条件过细", "条件过多", "范围过窄")),
    ("无结果", ("无结果", "结果为空", "没有数据", "未查询到")),
    ("内部执行异常", ("执行异常", "内部异常", "系统异常")),
]


def build_recommendation_context(
    upstream_result: Mapping[str, Any],
    failure_reason: str = "",
    failure_detail: str = "",
) -> RecommendationContext:
    """
    将上一步完整意图结构转换为推荐模块的最小化上下文。

    ``tenant``、``subnet``、``link_relation``、子部件名称和未知字段会被忽略。
    """
    data = upstream_result if isinstance(upstream_result, Mapping) else {}
    devices = _mapping_list(data.get("devices"))
    subcomponents = _mapping_list(data.get("subcomponents"))
    failure_type = _detect_failure_type(failure_reason, failure_detail)
    invalid_values = _extract_invalid_values(
        failure_type=failure_type,
        failure_text=f"{failure_reason} {failure_detail}".strip(),
        devices=devices,
        kpis=_string_list(data.get("kpis")),
        properties=_string_list(data.get("properties")),
    )

    identifiers = []
    for item in devices:
        identifier = Identifier.from_dict(
            {
                "value": item.get("device_id"),
                "id_type": item.get("id_type"),
                "match_mode": item.get("match_mode"),
            }
        )
        if identifier.value and identifier.value not in invalid_values:
            identifiers.append(identifier)

    time_value = data.get("time")
    if isinstance(time_value, (Mapping, list)):
        time_text = json.dumps(time_value, ensure_ascii=False)
    else:
        time_text = str(time_value or "").strip()

    return RecommendationContext(
        intention=str(data.get("intention", "") or "").strip(),
        question=str(data.get("question", "") or "").strip(),
        device_types=_dedupe(item.get("device_type") for item in devices),
        subcomponent_types=_dedupe(item.get("subcomponent_type") for item in subcomponents),
        identifiers=identifiers,
        properties=_string_list(data.get("properties")),
        kpis=_string_list(data.get("kpis")),
        time=time_text,
        alarm=AlarmCondition.from_dict(data.get("alarm")),
        aggregations=_normalize_aggregations(data.get("agg")),
        tables=_string_list(data.get("tables")),
        failure_type=failure_type,
        failure_summary=_failure_summary(failure_reason, failure_detail),
        invalid_values=invalid_values,
    )


def _detect_failure_type(reason: str, detail: str) -> str:
    text = f"{reason or ''} {detail or ''}".lower()
    for failure_type, patterns in FAILURE_PATTERNS:
        if any(pattern.lower() in text for pattern in patterns):
            return failure_type
    return "其他失败" if text.strip() else ""


def _extract_invalid_values(
    failure_type: str,
    failure_text: str,
    devices: List[Mapping[str, Any]],
    kpis: List[str],
    properties: List[str],
) -> List[str]:
    if failure_type in {"匹配到多设备", "业务域不明确", "时间缺失", "条件过细", "无结果"}:
        return []

    values: List[str] = []
    if failure_type in {"对象定位失败", "父对象定位失败"}:
        for item in devices:
            value = str(item.get("device_id", "") or "").strip()
            if value:
                values.append(value)
    elif failure_type == "指标不支持":
        values.extend(kpis)
    elif failure_type == "属性不支持":
        values.extend(properties)

    values.extend(re.findall(r"(?:IP|MAC|名称|指标|属性)[为是：:\s]*[\"“']?([0-9A-Za-z_.:-]+)", failure_text))
    return _dedupe(values)


def _failure_summary(reason: str, detail: str) -> str:
    return "；".join(item.strip() for item in (reason, detail) if item and item.strip())


def _normalize_aggregations(value: Any) -> List[str]:
    result = []
    for item in _string_list(value):
        normalized = AGGREGATION_ALIASES.get(item.lower(), item.lower())
        if normalized not in result:
            result.append(normalized)
    return result


def _mapping_list(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return _dedupe(values)


def _dedupe(values: Any) -> List[str]:
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
