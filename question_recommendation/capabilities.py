"""六类能力卡推荐的稳定公共入口。"""

from typing import List, Sequence, Tuple

from .capability_candidates import global_basic_fallback_candidates
from .capability_constants import (
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .capability_loader import load_capability_cards
from .capability_matching import dedupe_candidates
from .capability_ranking import RankedCapability, rank_candidates, select_diverse
from .capability_recall import recall_candidates
from .capability_routing import resolve_primary_capability_type
from .models import (
    DeviceCapabilityProfile,
    MetadataTable,
    RecommendationContext,
    SpecialCapabilitySpec,
)
from .refusal_rules import BASIC


def recommend_capabilities(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable] = (),
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    special_cards: Sequence[SpecialCapabilitySpec] = (),
    limit: int = 12,
) -> List[RankedCapability]:
    """根据标准上下文生成、过滤、排序并选择动态候选能力。"""
    if limit <= 0:
        return []
    resolved_domain_cards, resolved_special_cards = _resolve_capability_cards(
        domain_cards, special_cards
    )
    candidates = dedupe_candidates(
        recall_candidates(context, resolved_domain_cards, resolved_special_cards)
    )
    if not candidates and context.recovery_strategy == BASIC:
        candidates = global_basic_fallback_candidates(resolved_domain_cards)
    ranked = rank_candidates(context, candidates, metadata_tables)
    return select_diverse(ranked, limit)


def _resolve_capability_cards(
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> Tuple[List[DeviceCapabilityProfile], List[SpecialCapabilitySpec]]:
    """保留已注入卡片，并通过一次文件读取补齐缺失卡片。"""
    resolved_domain_cards = list(domain_cards)
    resolved_special_cards = list(special_cards)
    if resolved_domain_cards and resolved_special_cards:
        return resolved_domain_cards, resolved_special_cards
    loaded_domain_cards, loaded_special_cards = load_capability_cards()
    if not resolved_domain_cards:
        resolved_domain_cards = loaded_domain_cards
    if not resolved_special_cards:
        resolved_special_cards = loaded_special_cards
    return resolved_domain_cards, resolved_special_cards
