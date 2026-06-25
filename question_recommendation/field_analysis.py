"""在调用 LLM 前分析最终候选对原查询字段的精确支持情况。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

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
        "unsupported_properties": _unsupported_candidate_fields(
            context,
            candidate_capabilities,
            requested_fields=context.properties,
            field_name="properties",
        ),
        "unsupported_kpis": _unsupported_candidate_fields(
            context,
            candidate_capabilities,
            requested_fields=context.kpis,
            field_name="metrics",
        ),
    }


@dataclass(frozen=True)
class _FieldMatch:
    """原问题中命中的候选字段及其候选归属。"""

    field: str
    start: int
    end: int
    has_subcomponent_scope: bool


def _unsupported_candidate_fields(
    context: RecommendationContext,
    candidate_capabilities: Sequence[Mapping[str, Any]],
    requested_fields: Sequence[str],
    field_name: str,
) -> List[str]:
    """分析结构化字段；缺失时从原问题做候选字段轻量抽取。"""
    if requested_fields:
        return _unsupported_fields(requested_fields, candidate_capabilities, field_name)
    inferred_fields = _infer_unsupported_question_fields(
        context, candidate_capabilities, field_name
    )
    return inferred_fields


def _unsupported_fields(
    requested_fields: Sequence[str],
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> List[str]:
    """保留未被任何最终候选精确支持的结构化原始查询字段。"""
    supported_fields = _candidate_field_set(candidate_capabilities, field_name)
    result: List[str] = []
    for field in requested_fields:
        if normalized_set([field]).isdisjoint(supported_fields):
            result.append(field)
    return result


def _infer_unsupported_question_fields(
    context: RecommendationContext,
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> List[str]:
    """从原问题保守抽取字段，并判断其候选归属是否兼容。"""
    if not context.question:
        return []
    matches = _question_field_matches(context.question, candidate_capabilities, field_name)
    if not matches:
        return []
    requested_subcomponent = _question_requests_subcomponent(context, candidate_capabilities)
    supported_fields = _compatible_field_set(matches, requested_subcomponent)
    result: List[str] = []
    for field in _matched_fields_in_order(matches):
        if normalized_set([field]).isdisjoint(supported_fields):
            result.append(field)
    return result


def _question_field_matches(
    question: str,
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> List[_FieldMatch]:
    """返回问题中命中的最长候选字段。"""
    normalized_question = _match_key(question)
    matches: List[_FieldMatch] = []
    for candidate in candidate_capabilities:
        scoped = _has_subcomponent_scope(candidate)
        for field in _candidate_values(candidate, field_name):
            matches.extend(_field_occurrences(normalized_question, field, scoped))
    return _longest_field_matches(matches)


def _field_occurrences(
    normalized_question: str,
    field: Any,
    has_subcomponent_scope: bool,
) -> List[_FieldMatch]:
    """返回单个字段在问题中的所有命中。"""
    normalized_field = _match_key(field)
    if not normalized_field:
        return []
    matches: List[_FieldMatch] = []
    start = normalized_question.find(normalized_field)
    while start >= 0:
        end = start + len(normalized_field)
        matches.append(
            _FieldMatch(
                field=str(field).strip(),
                start=start,
                end=end,
                has_subcomponent_scope=has_subcomponent_scope,
            )
        )
        start = normalized_question.find(normalized_field, start + 1)
    return matches


def _longest_field_matches(matches: Sequence[_FieldMatch]) -> List[_FieldMatch]:
    """删除被更长字段完整覆盖的短字段命中。"""
    result: List[_FieldMatch] = []
    for match in matches:
        if _field_match_is_covered(match, matches):
            continue
        result.append(match)
    return result


def _field_match_is_covered(
    match: _FieldMatch,
    matches: Sequence[_FieldMatch],
) -> bool:
    """判断字段命中是否被更长字段覆盖。"""
    for other in matches:
        if other.start > match.start or other.end < match.end:
            continue
        if len(_match_key(other.field)) > len(_match_key(match.field)):
            return True
    return False


def _question_requests_subcomponent(
    context: RecommendationContext,
    candidate_capabilities: Sequence[Mapping[str, Any]],
) -> bool:
    """判断用户问题是否明确以子部件为查询对象。"""
    if context.subcomponent_types:
        return True
    normalized_question = _match_key(context.question)
    for candidate in candidate_capabilities:
        for subcomponent in _candidate_values(candidate, "subcomponent_types"):
            if _match_key(subcomponent) and _match_key(subcomponent) in normalized_question:
                return True
    return False


def _compatible_field_set(
    matches: Sequence[_FieldMatch],
    requested_subcomponent: bool,
) -> set:
    """根据查询对象层级返回兼容字段集合。"""
    values: List[str] = []
    for match in matches:
        if match.has_subcomponent_scope != requested_subcomponent:
            continue
        values.append(match.field)
    return normalized_set(values)


def _matched_fields_in_order(matches: Sequence[_FieldMatch]) -> List[str]:
    """按命中顺序返回字段名并去重。"""
    result: List[str] = []
    seen = set()
    sorted_matches = sorted(matches, key=_field_sort_key)
    for match in sorted_matches:
        key = _match_key(match.field)
        if key in seen:
            continue
        seen.add(key)
        result.append(match.field)
    return result


def _field_sort_key(match: _FieldMatch) -> Tuple[int, int, str]:
    """字段命中的稳定排序 key。"""
    return (match.start, -(match.end - match.start), match.field)


def _candidate_field_set(
    candidate_capabilities: Sequence[Mapping[str, Any]],
    field_name: str,
) -> set:
    """汇总最终候选指定字段中的全部非空值。"""
    values: List[Any] = []
    for candidate in candidate_capabilities:
        values.extend(_candidate_values(candidate, field_name))
    return normalized_set(values)


def _candidate_values(candidate: Mapping[str, Any], field_name: str) -> List[Any]:
    """返回候选字段列表。"""
    values = candidate.get(field_name, [])
    if isinstance(values, (list, tuple, set)):
        return list(values)
    return []


def _has_subcomponent_scope(candidate: Mapping[str, Any]) -> bool:
    """判断候选字段是否属于子部件对象。"""
    return bool(_candidate_values(candidate, "subcomponent_types"))


def _match_key(value: Any) -> str:
    """生成字段匹配 key。"""
    return str(value or "").strip().casefold()


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
