"""在调用 LLM 前分析最终候选对原查询字段的精确支持情况。"""

import re
from typing import Any, Dict, List, Mapping, Sequence

from .capability_matching import normalize_match_value, normalized_set
from .models import MetadataTable, RecommendationContext


def analyze_candidate_fields(
    context: RecommendationContext,
    candidate_capabilities: Sequence[Mapping[str, Any]],
    metadata_tables: Sequence[MetadataTable] = (),
) -> Dict[str, List[str]]:
    """返回无实时元数据时被全部最终候选精确拒绝的属性和 KPI。"""
    if _has_usable_metadata(metadata_tables):
        return _empty_analysis()
    analysis = {
        "unsupported_properties": _unsupported_fields(
            context.properties, candidate_capabilities, "properties"
        ),
        "unsupported_kpis": _unsupported_fields(
            context.kpis, candidate_capabilities, "metrics"
        ),
    }
    unsupported_question_terms = _unsupported_question_terms(
        context.question, candidate_capabilities
    )
    if unsupported_question_terms:
        analysis["unsupported_question_terms"] = unsupported_question_terms
    return analysis


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


def _unsupported_question_terms(
    question: str,
    candidate_capabilities: Sequence[Mapping[str, Any]],
) -> List[str]:
    """识别原问题中“X相关”但最终候选不支持的修饰词。"""
    supported_terms = _candidate_supported_terms(candidate_capabilities)
    result: List[str] = []
    for term in _related_terms(question):
        if not _term_supported(term, supported_terms) and term not in result:
            result.append(term)
    return result


def _related_terms(question: str) -> List[str]:
    """抽取“X相关”中的 X，并清理常见查询前缀。"""
    result: List[str] = []
    for match in re.finditer(r"([^，。！？、\s的]+?)相关", question or ""):
        term = _strip_question_prefix(match.group(1))
        if term and term not in result:
            result.append(term)
    return result


def _strip_question_prefix(term: str) -> str:
    """去除“查询/查看”等可能贴在修饰词前的动词。"""
    text = str(term or "").strip()
    for prefix in ("查询", "查看", "获取", "统计", "请问", "看下", "帮我查"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def _candidate_supported_terms(
    candidate_capabilities: Sequence[Mapping[str, Any]],
) -> List[str]:
    """汇总候选中可作为继承依据的对象、字段和示例文本。"""
    result: List[str] = []
    for candidate in candidate_capabilities:
        for field_name in (
            "device_types",
            "subcomponent_types",
            "objects",
            "properties",
            "metrics",
            "examples",
        ):
            values = candidate.get(field_name, [])
            if isinstance(values, (list, tuple, set)):
                result.extend(str(value or "").strip() for value in values if value)
    return result


def _term_supported(term: str, supported_terms: Sequence[str]) -> bool:
    """判断抽取词是否被候选明确支持。"""
    normalized_term = normalize_match_value(term)
    if not normalized_term:
        return True
    for value in supported_terms:
        normalized_value = normalize_match_value(value)
        if not normalized_value:
            continue
        if normalized_term == normalized_value:
            return True
        if normalized_term in normalized_value or normalized_value in normalized_term:
            return True
    return False


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
