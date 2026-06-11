"""空意图、拒答方向和常规能力候选召回。"""

from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

from .capability_candidates import (
    adjacent_candidates,
    domain_card_candidates,
    primary_candidates,
    special_candidates,
    subcomponent_candidate,
)
from .capability_constants import (
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    KPI_RELAXING_RECOVERY_STRATEGIES,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .capability_matching import (
    contains_capability_type,
    contains_any,
    context_device_types,
    dedupe_domain_cards,
    device_conditions_for_types,
    domain_card_device_terms,
    domain_card_ids,
    domain_card_standard_device_types,
    domain_cards_matching_question_direction,
    matching_domain_cards,
    specific_terms_in_text,
    subcomponents_matching_text,
)
from .capability_routing import resolve_primary_capability_type
from .models import (
    CapabilityCandidate,
    DeviceCapabilityProfile,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)
from .refusal_rules import BASIC


def recall_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[CapabilityCandidate]:
    """依次执行对象方向、拒答方向和常规能力召回。"""
    candidates = _empty_intention_basic_candidates(
        context, domain_cards, special_cards
    )
    if candidates is not None:
        return candidates
    candidates = _recovery_question_direction_candidates(
        context, domain_cards, special_cards
    )
    if candidates is not None:
        return candidates
    return _regular_candidates(context, domain_cards, special_cards)


def _regular_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[CapabilityCandidate]:
    """召回已有结构化对象下的主能力和相邻能力。"""
    matched_domain_cards = matching_domain_cards(context, domain_cards)
    primary_type = resolve_primary_capability_type(context)
    candidates = primary_candidates(
        context, matched_domain_cards, special_cards, primary_type
    )
    candidates.extend(
        adjacent_candidates(
            context, matched_domain_cards, special_cards, primary_type
        )
    )
    return candidates


def _empty_intention_basic_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> Optional[List[CapabilityCandidate]]:
    """按空意图 Basic 原问题中的明确业务对象收敛基础候选。"""
    if not _uses_empty_intention_basic_direction(context):
        return None
    matched_domain_cards = domain_cards_matching_question_direction(
        context.question, domain_cards
    )
    matched_subcomponents = subcomponents_matching_text(context.question, domain_cards)
    matched_special_cards = _special_cards_matching_text(context.question, special_cards)
    if not matched_domain_cards and not matched_subcomponents and not matched_special_cards:
        return None
    special_result = _basic_special_candidates(
        context, matched_domain_cards, matched_special_cards, domain_cards
    )
    if special_result:
        return special_result
    matched_subcomponents = _constrain_subcomponent_matches(
        matched_domain_cards, matched_subcomponents
    )
    if matched_subcomponents:
        return _basic_subcomponent_candidates(context, matched_subcomponents)
    return _basic_domain_candidates(context, matched_domain_cards)


def _uses_empty_intention_basic_direction(context: RecommendationContext) -> bool:
    """判断是否应从空意图 Basic 原问题补充对象方向。"""
    return bool(
        context.recovery_strategy == BASIC
        and not context.intention
        and context.question
    )


def _special_cards_matching_text(
    text: str,
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[SpecialCapabilitySpec]:
    """返回对象词出现在原问题中的特殊卡。"""
    matched = []
    for special_card in special_cards:
        if contains_any(text, special_card.objects):
            matched.append(special_card)
    return matched


def _basic_special_candidates(
    context: RecommendationContext,
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
    matched_special_cards: Sequence[SpecialCapabilitySpec],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的特殊能力候选。"""
    if not matched_special_cards:
        return []
    device_values = specific_terms_in_text(
        context.question, domain_card_device_terms(domain_cards)
    )
    if not device_values:
        device_values = domain_card_standard_device_types(matched_domain_cards)
    special_context = _basic_special_context(
        context.question, device_values, matched_special_cards
    )
    candidates = []
    for special_card in matched_special_cards:
        candidates.extend(
            special_candidates(
                special_context,
                [special_card],
                special_card.capability_type,
                domain_cards,
            )
        )
    return candidates


def _basic_special_context(
    question: str,
    device_types: Sequence[str],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> RecommendationContext:
    """构造仅用于特殊卡匹配的空意图 Basic 上下文。"""
    special_objects = []
    for special_card in special_cards:
        special_objects.extend(special_card.objects)
    return RecommendationContext(
        question=question,
        devices=device_conditions_for_types(device_types),
        subcomponent_types=special_objects,
    )


def _constrain_subcomponent_matches(
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
    matched_subcomponents: Sequence[
        Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]
    ],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """存在设备方向时，仅保留其兼容的子部件。"""
    if not matched_domain_cards:
        return list(matched_subcomponents)
    card_ids = domain_card_ids(matched_domain_cards)
    result = []
    for domain_card, subcomponent_card in matched_subcomponents:
        if domain_card.profile_id in card_ids:
            result.append((domain_card, subcomponent_card))
    return result


def _basic_subcomponent_candidates(
    context: RecommendationContext,
    matched_subcomponents: Sequence[
        Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]
    ],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的子部件信息和数量候选。"""
    candidates = []
    for domain_card, subcomponent_card in matched_subcomponents:
        for capability_type in (SUBCOMPONENT_INFO, SUBCOMPONENT_COUNT):
            candidate = subcomponent_candidate(
                context, domain_card, subcomponent_card, capability_type, relax=True
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _basic_domain_candidates(
    context: RecommendationContext,
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的设备信息和数量候选。"""
    candidates = []
    for domain_card in matched_domain_cards:
        for capability_type in (DEVICE_INFO, DEVICE_COUNT):
            candidates.extend(
                domain_card_candidates(
                    context, domain_card, capability_type, relax=True
                )
            )
    return candidates


def _recovery_question_direction_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> Optional[List[CapabilityCandidate]]:
    """在拒答且无结构化对象时，按原问题中的能力卡方向收敛候选。"""
    if not _uses_recovery_question_direction(context):
        return None
    matched_domain_cards, matched_subcomponents = _recovery_direction_matches(
        context.question, domain_cards
    )
    if not matched_domain_cards:
        return None
    direction_context = _build_direction_context(
        context, matched_domain_cards, matched_subcomponents
    )
    return _direction_candidates(
        context, direction_context, matched_domain_cards, special_cards
    )


def _uses_recovery_question_direction(context: RecommendationContext) -> bool:
    """判断拒答场景是否需要从原问题补充业务方向。"""
    return bool(
        context.recovery_strategy
        and not context_device_types(context)
        and not context.subcomponent_types
        and context.question
    )


def _recovery_direction_matches(
    question: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> Tuple[
    List[DeviceCapabilityProfile],
    List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]],
]:
    """解析拒答原问题中的领域卡和子部件方向。"""
    matched_cards = domain_cards_matching_question_direction(question, domain_cards)
    matched_subcomponents = subcomponents_matching_text(question, domain_cards)
    if matched_cards:
        matched_subcomponents = _constrain_subcomponent_matches(
            matched_cards, matched_subcomponents
        )
    elif matched_subcomponents:
        parent_cards = []
        for domain_card, _ in matched_subcomponents:
            parent_cards.append(domain_card)
        matched_cards = dedupe_domain_cards(parent_cards)
    return matched_cards, matched_subcomponents


def _build_direction_context(
    context: RecommendationContext,
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
    matched_subcomponents: Sequence[
        Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]
    ],
) -> RecommendationContext:
    """使用识别到的业务方向构造临时召回上下文。"""
    subcomponent_types = []
    for _, subcomponent_card in matched_subcomponents:
        subcomponent_types.extend(subcomponent_card.types)
    return replace(
        context,
        devices=device_conditions_for_types(
            domain_card_standard_device_types(matched_domain_cards)
        ),
        subcomponent_types=subcomponent_types,
    )


def _direction_candidates(
    context: RecommendationContext,
    direction_context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[CapabilityCandidate]:
    """生成拒答问题方向内的主候选、相邻候选和放宽指标候选。"""
    primary_type = resolve_primary_capability_type(direction_context)
    candidates = primary_candidates(
        direction_context, domain_cards, special_cards, primary_type
    )
    candidates.extend(
        adjacent_candidates(direction_context, domain_cards, special_cards, primary_type)
    )
    _append_relaxed_metric_candidates(
        candidates, context, direction_context, domain_cards, special_cards, primary_type
    )
    return candidates


def _append_relaxed_metric_candidates(
    candidates: List[CapabilityCandidate],
    context: RecommendationContext,
    direction_context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> None:
    """指标不清晰且方向明确时，补充忽略错误 KPI 的同方向指标候选。"""
    if primary_type not in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return
    if context.recovery_strategy not in KPI_RELAXING_RECOVERY_STRATEGIES:
        return
    if contains_capability_type(candidates, primary_type):
        return
    relaxed_context = replace(direction_context, kpis=[])
    candidates.extend(
        primary_candidates(relaxed_context, domain_cards, special_cards, primary_type)
    )
