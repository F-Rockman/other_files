"""六类能力卡推荐的稳定公共入口。"""

from typing import List, Optional, Sequence, Tuple

from .capability_candidates import global_basic_fallback_candidates
from .capability_constants import (
    ALARM_QUERY,
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    LINK_QUERY,
    RELATION_QUERY,
    RESOURCE_QUERY,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .capability_loader import load_capability_cards
from .capability_matching import context_device_types, dedupe_candidates, is_subnet_context
from .capability_ranking import RankedCapability, rank_candidates, select_diverse
from .capability_recall import recall_candidates
from .capability_routing import resolve_primary_capability_type
from .metadata_loader import PathProvider
from .models import (
    CapabilityCandidate,
    DeviceCapabilityProfile,
    MetadataTable,
    RecommendationContext,
    SpecialCapabilitySpec,
)
from .refusal_rules import BASIC, SIMPLIFY


_DEVICE_TASK_TYPES = {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}
_SUBCOMPONENT_TASK_TYPES = {
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_METRIC,
}
_LINK_TASK_TYPES = {LINK_QUERY, RELATION_QUERY}
_SUBNET_TASK_TYPES = {RESOURCE_QUERY, RELATION_QUERY}


def recommend_capabilities(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable] = (),
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    special_cards: Sequence[SpecialCapabilitySpec] = (),
    logical_model_path_provider: Optional[PathProvider] = None,
    limit: int = 12,
) -> List[RankedCapability]:
    """根据标准上下文生成、过滤、排序并选择动态候选能力。"""
    if limit <= 0:
        return []
    resolved_domain_cards, resolved_special_cards = _resolve_capability_cards(
        domain_cards, special_cards, logical_model_path_provider
    )
    candidates = dedupe_candidates(
        recall_candidates(context, resolved_domain_cards, resolved_special_cards)
    )
    candidates = _filter_simplify_candidates(context, candidates)
    if not candidates and context.recovery_strategy == BASIC:
        candidates = global_basic_fallback_candidates(resolved_domain_cards)
    ranked = rank_candidates(context, candidates, metadata_tables)
    return select_diverse(ranked, limit)


def _resolve_capability_cards(
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    logical_model_path_provider: Optional[PathProvider],
) -> Tuple[List[DeviceCapabilityProfile], List[SpecialCapabilitySpec]]:
    """保留已注入卡片，并通过一次文件读取补齐缺失卡片。"""
    resolved_domain_cards = list(domain_cards)
    resolved_special_cards = list(special_cards)
    if resolved_domain_cards and resolved_special_cards:
        return resolved_domain_cards, resolved_special_cards
    loaded_domain_cards, loaded_special_cards = load_capability_cards(
        logical_model_path_provider
    )
    if not resolved_domain_cards:
        resolved_domain_cards = loaded_domain_cards
    if not resolved_special_cards:
        resolved_special_cards = loaded_special_cards
    return resolved_domain_cards, resolved_special_cards


def _filter_simplify_candidates(
    context: RecommendationContext,
    candidates: Sequence[CapabilityCandidate],
) -> List[CapabilityCandidate]:
    """在 simplify 场景中只保留同任务族候选。"""
    if context.recovery_strategy != SIMPLIFY:
        return list(candidates)
    allowed_types = _simplify_allowed_capability_types(context)
    if not allowed_types:
        return list(candidates)
    result: List[CapabilityCandidate] = []
    for candidate in candidates:
        if candidate.capability_type in allowed_types:
            result.append(candidate)
    return result


def _simplify_allowed_capability_types(context: RecommendationContext) -> set:
    """根据结构化上下文确定 simplify 原任务族。"""
    if context.intention == "查告警":
        return {ALARM_QUERY}
    if context.intention == "查链路":
        return set(_LINK_TASK_TYPES)
    if is_subnet_context(context):
        return set(_SUBNET_TASK_TYPES)
    if context.subcomponent_types:
        return set(_SUBCOMPONENT_TASK_TYPES)
    if context_device_types(context):
        return set(_DEVICE_TASK_TYPES)
    if context.intention in {"查信息", "查指标"}:
        return set(_DEVICE_TASK_TYPES)
    return set()
