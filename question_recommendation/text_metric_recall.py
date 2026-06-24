"""空意图文本指标命中的设备与子部件候选生成。"""

from dataclasses import replace
from typing import List, Sequence, Tuple

from .capability_candidates import domain_card_candidates, subcomponent_candidate
from .capability_constants import DEVICE_METRIC, SUBCOMPONENT_METRIC
from .capability_matching import (
    contains_any,
    context_device_types,
    domain_card_ids,
    generic_subcomponent_metrics_matching_text,
    subcomponent_metrics_matching_text,
)
from .models import (
    CapabilityCandidate,
    DeviceCapabilityProfile,
    RecommendationContext,
    SubcomponentCapabilitySpec,
)


def explicit_subcomponent_metric_matches(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """返回空意图文本中明确锚定子部件的指标命中。"""
    if context_device_types(context) or context.subcomponent_types:
        return []
    matches = subcomponent_metrics_matching_text(context.question, domain_cards)
    return _constrain_matches(matched_domain_cards, matches)


def generic_metric_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """泛指标命中时，同时保留设备指标和子部件指标候选。"""
    if context_device_types(context) or context.subcomponent_types:
        return []
    metric_domain_cards = _domain_cards_matching_device_metrics(
        context.question, matched_domain_cards or domain_cards
    )
    matches = generic_subcomponent_metrics_matching_text(context.question, domain_cards)
    metric_subcomponents = _constrain_matches(matched_domain_cards, matches)
    if not metric_domain_cards and not metric_subcomponents:
        return []
    candidates = _device_metric_candidates(context, metric_domain_cards)
    candidates.extend(subcomponent_metric_candidates(context, metric_subcomponents))
    return candidates


def subcomponent_metric_candidates(
    context: RecommendationContext,
    matched_subcomponents: Sequence[
        Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]
    ],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的子部件指标候选。"""
    candidates = []
    metric_context = replace(context, kpis=[])
    for domain_card, subcomponent_card in matched_subcomponents:
        candidate = subcomponent_candidate(
            metric_context,
            domain_card,
            subcomponent_card,
            SUBCOMPONENT_METRIC,
            relax=True,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _device_metric_candidates(
    context: RecommendationContext,
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的设备指标候选。"""
    candidates = []
    metric_context = replace(context, kpis=[])
    for domain_card in matched_domain_cards:
        candidates.extend(
            domain_card_candidates(
                metric_context, domain_card, DEVICE_METRIC, relax=True
            )
        )
    return candidates


def _domain_cards_matching_device_metrics(
    question: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """返回原问题命中设备级指标名的领域卡。"""
    result = []
    for domain_card in domain_cards:
        if contains_any(question, domain_card.metrics):
            result.append(domain_card)
    return result


def _constrain_matches(
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
    matched_subcomponents: Sequence[
        Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]
    ],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """存在设备方向时，仅保留其兼容的子部件指标。"""
    if not matched_domain_cards:
        return list(matched_subcomponents)
    card_ids = domain_card_ids(matched_domain_cards)
    result = []
    for domain_card, subcomponent_card in matched_subcomponents:
        if domain_card.profile_id in card_ids:
            result.append((domain_card, subcomponent_card))
    return result
