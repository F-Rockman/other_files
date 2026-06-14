"""在调用 LLM 前分析最终候选对原查询字段的精确支持情况。"""

from typing import Any, Dict, List, Mapping, Sequence

from .capability_matching import normalized_set
from .models import MetadataTable, RecommendationContext


def analyze_candidate_fields(
    context: RecommendationContext,
    candidate_capabilities: Sequence[Mapping[str, Any]],
    metadata_tables: Sequence[MetadataTable] = (),
) -> Dict[str, List[str]]:
    """返回无实时元数据时被全部最终候选精确拒绝的属性和 KPI。"""
    if _has_usable_metadata(metadata_tables):
        return _empty_analysis()
    return {
        "unsupported_properties": _unsupported_fields(
            context.properties, candidate_capabilities, "properties"
        ),
        "unsupported_kpis": _unsupported_fields(
            context.kpis, candidate_capabilities, "metrics"
        ),
    }


def _unsupported_fields(
    requested_fields: Sequence[str],
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> List[str]:
    """保留未被任何最终候选精确支持的原始查询字段。"""
    supported_fields = _candidate_field_set(candidate_capabilities, field_name)
    result: List[str] = []
    for field in requested_fields:
        if normalized_set([field]).isdisjoint(supported_fields):
            result.append(field)
    return result


def _candidate_field_set(
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> set:
    """汇总最终候选指定字段中的全部非空值。"""
    values: List[Any] = []
    for candidate in candidate_capabilities:
        candidate_values = candidate.get(field_name, [])
        if isinstance(candidate_values, (list, tuple, set)):
            values.extend(candidate_values)
    return normalized_set(values)


def _has_usable_metadata(metadata_tables: Sequence[MetadataTable]) -> bool:
    """判断实时元数据中是否存在至少一个可面向用户表达的字段描述。"""
    for table in metadata_tables:
        for column in table.columns:
            if str(column.column_description or "").strip():
                return True
    return False


def _empty_analysis() -> Dict[str, List[str]]:
    """返回不包含未命中字段的稳定分析结构。"""
    return {"unsupported_properties": [], "unsupported_kpis": []}
