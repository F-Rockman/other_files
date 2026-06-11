"""候选能力的确定性评分、排序和多样性选择。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .capability_matching import (
    context_device_types,
    has_overlap,
    normalize_match_value,
    values_equal,
)
from .capability_routing import resolve_primary_capability_type
from .models import CapabilityCandidate, MetadataTable, RecommendationContext


@dataclass
class RankedCapability:
    """包含动态候选能力和内部确定性分数的排序结果。"""

    candidate: CapabilityCandidate
    match_score: int

    def to_dict(self) -> Dict[str, Any]:
        """生成精简 Prompt 候选，不暴露内部排序字段和元数据提示。"""
        data = self.candidate.to_dict()
        data.pop("table_hints", None)
        data.pop("priority", None)
        return data


def rank_candidates(
    context: RecommendationContext,
    candidates: Sequence[CapabilityCandidate],
    metadata_tables: Sequence[MetadataTable],
) -> List[RankedCapability]:
    """计算候选分数并按稳定规则排序。"""
    ranked = []
    for candidate in candidates:
        ranked.append(_rank_candidate(context, candidate, metadata_tables))
    ranked.sort(key=_rank_sort_key)
    return ranked


def _rank_sort_key(item: RankedCapability) -> Tuple[int, int, str]:
    """返回候选的稳定排序键。"""
    return (
        -item.match_score,
        -item.candidate.priority,
        item.candidate.capability_id,
    )


def _rank_candidate(
    context: RecommendationContext,
    candidate: CapabilityCandidate,
    metadata_tables: Sequence[MetadataTable],
) -> RankedCapability:
    """计算动态候选与上下文的确定性相关分数。"""
    score = candidate.priority + _context_match_score(context, candidate)
    if _metadata_matches(candidate.table_hints, context.tables, metadata_tables):
        score += 30
    return RankedCapability(candidate=candidate, match_score=score)


def _context_match_score(
    context: RecommendationContext,
    candidate: CapabilityCandidate,
) -> int:
    """计算不含优先级和元数据的上下文匹配分数。"""
    score = 0
    if values_equal(candidate.capability_type, resolve_primary_capability_type(context)):
        score += 160
    if has_overlap(context_device_types(context), candidate.device_types):
        score += 120
    if has_overlap(context.subcomponent_types, candidate.subcomponent_types):
        score += 100
    if context.kpis and has_overlap(context.kpis, candidate.metrics):
        score += 60
    if context.properties and has_overlap(context.properties, candidate.properties):
        score += 40
    return score


def _metadata_matches(
    hints: Sequence[str],
    table_names: Sequence[str],
    metadata_tables: Sequence[MetadataTable],
) -> bool:
    """判断候选表提示是否命中逻辑表名、表描述或字段描述。"""
    metadata_texts = list(table_names)
    for table in metadata_tables:
        _append_nonempty(metadata_texts, table.table_name)
        _append_nonempty(metadata_texts, table.table_description)
        for column in table.columns:
            _append_nonempty(metadata_texts, column.column_name)
            _append_nonempty(metadata_texts, column.column_description)
    flattened = normalize_match_value(" ".join(metadata_texts))
    for hint in hints:
        normalized_hint = normalize_match_value(hint)
        if normalized_hint and normalized_hint in flattened:
            return True
    return False


def _append_nonempty(values: List[str], value: str) -> None:
    """将非空文本追加到元数据匹配文本列表。"""
    if value:
        values.append(value)


def select_diverse(
    ranked: Sequence[RankedCapability],
    limit: int,
) -> List[RankedCapability]:
    """按能力骨架和对象族限制重复，选择稳定且有差异的 Top N。"""
    selected: List[RankedCapability] = []
    group_counts: Dict[Tuple[str, str, str], int] = {}
    for item in ranked:
        key = _candidate_group_key(item.candidate)
        if group_counts.get(key, 0) >= 2:
            continue
        selected.append(item)
        group_counts[key] = group_counts.get(key, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def _candidate_group_key(candidate: CapabilityCandidate) -> Tuple[str, str, str]:
    """返回用于限制重复候选的能力骨架和对象族键。"""
    device_type = candidate.device_types[0] if candidate.device_types else ""
    subcomponent_type = (
        candidate.subcomponent_types[0] if candidate.subcomponent_types else ""
    )
    return candidate.capability_type, device_type, subcomponent_type
