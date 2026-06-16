"""设备、子部件和特殊能力候选生成。"""

from typing import List, Optional, Sequence

from .capability_constants import (
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    RELATION_QUERY,
    RESOURCE_QUERY,
    SPECIAL_CAPABILITY_TYPES,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .capability_matching import (
    contains_any,
    context_device_types,
    dedupe_candidates,
    domain_card_alias_supported,
    examples_for_type,
    has_overlap,
    is_subnet_context,
    locators_compatible,
    normalize_match_value,
    normalized_set,
    subcomponent_matches_any,
    values_equal,
)
from .models import (
    CapabilityCandidate,
    DeviceCapabilityProfile,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)


def primary_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """生成主查询骨架对应的设备或特殊能力候选。"""
    if primary_type in SPECIAL_CAPABILITY_TYPES:
        return special_candidates(context, special_cards, primary_type, domain_cards)
    candidates = []
    for domain_card in domain_cards:
        candidates.extend(domain_card_candidates(context, domain_card, primary_type))
    return candidates


def adjacent_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """在主能力附近补充同对象、低成本且语义不同的候选能力。"""
    candidates: List[CapabilityCandidate] = []
    for domain_card in domain_cards:
        for capability_type in _adjacent_capability_types(context, primary_type):
            if capability_type == primary_type:
                continue
            candidates.extend(
                domain_card_candidates(
                    context, domain_card, capability_type, relax=True
                )
            )
    if context.intention == "查信息" or context.subnet:
        candidates.extend(relation_candidates(context, domain_cards, special_cards))
    return candidates


def _adjacent_capability_types(
    context: RecommendationContext,
    primary_type: str,
) -> List[str]:
    """返回主能力附近可补充的同对象能力类型。"""
    if context.subcomponent_types:
        adjacent_types = [SUBCOMPONENT_INFO, SUBCOMPONENT_COUNT]
        if primary_type == SUBCOMPONENT_INFO:
            adjacent_types.append(SUBCOMPONENT_METRIC)
        return adjacent_types
    adjacent_types = [DEVICE_INFO, DEVICE_COUNT]
    if primary_type == DEVICE_INFO:
        adjacent_types.append(DEVICE_METRIC)
    return adjacent_types


def global_basic_fallback_candidates(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """在 Basic 没有兼容候选时生成全局设备信息和数量候选。"""
    empty_context = RecommendationContext()
    candidates: List[CapabilityCandidate] = []
    for domain_card in domain_cards:
        candidates.extend(
            domain_card_candidates(empty_context, domain_card, DEVICE_INFO, relax=True)
        )
        candidates.extend(
            domain_card_candidates(empty_context, domain_card, DEVICE_COUNT, relax=True)
        )
    return dedupe_candidates(candidates)


def domain_card_candidates(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool = False,
) -> List[CapabilityCandidate]:
    """根据一个设备规格和查询骨架动态生成候选能力。"""
    if capability_type in {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}:
        candidate = _device_candidate(context, domain_card, capability_type, relax)
        return [candidate] if candidate else []
    if capability_type not in {
        SUBCOMPONENT_INFO,
        SUBCOMPONENT_COUNT,
        SUBCOMPONENT_METRIC,
    }:
        return []
    return _subcomponent_candidates(context, domain_card, capability_type, relax)


def _subcomponent_candidates(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool,
) -> List[CapabilityCandidate]:
    """生成一个领域卡下匹配的全部子部件候选。"""
    candidates = []
    for spec in _matching_subcomponents(context, domain_card):
        candidate = subcomponent_candidate(
            context, domain_card, spec, capability_type, relax
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _device_candidate(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool,
) -> Optional[CapabilityCandidate]:
    """生成设备信息、数量或指标候选。"""
    if not relax and not locators_compatible(context, domain_card.locators):
        return None
    metrics = _matching_metrics(context, domain_card.metrics, capability_type)
    if capability_type == DEVICE_METRIC and not metrics:
        return None
    return CapabilityCandidate(
        capability_id=f"{domain_card.profile_id}:{capability_type}",
        capability_type=capability_type,
        domain=domain_card.domain,
        device_types=domain_card.device_types,
        locators=domain_card.locators,
        properties=domain_card.properties if capability_type == DEVICE_INFO else [],
        metrics=metrics,
        table_hints=domain_card.table_hints,
        examples=examples_for_type(
            domain_card.examples, capability_type, domain_card.metrics
        ),
        priority=domain_card.priority,
    )


def subcomponent_candidate(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    spec: SubcomponentCapabilitySpec,
    capability_type: str,
    relax: bool,
) -> Optional[CapabilityCandidate]:
    """生成设备子部件信息、数量或指标候选。"""
    if not relax and not locators_compatible(context, domain_card.locators):
        return None
    metrics = _matching_metrics(context, spec.metrics, capability_type)
    if capability_type == SUBCOMPONENT_METRIC and not metrics:
        return None
    return CapabilityCandidate(
        capability_id=f"{domain_card.profile_id}:{_slug(spec.types)}:{capability_type}",
        capability_type=capability_type,
        domain=domain_card.domain,
        device_types=domain_card.device_types,
        subcomponent_types=spec.types,
        locators=domain_card.locators,
        properties=spec.properties if capability_type == SUBCOMPONENT_INFO else [],
        metrics=metrics,
        table_hints=domain_card.table_hints + spec.table_hints,
        examples=examples_for_type(spec.examples, capability_type, spec.metrics),
        priority=domain_card.priority + spec.priority,
    )


def _matching_subcomponents(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
) -> List[SubcomponentCapabilitySpec]:
    """返回与上下文对象匹配的嵌套子部件规格。"""
    if not context.subcomponent_types:
        return list(domain_card.subcomponents)
    matched = []
    for spec in domain_card.subcomponents:
        if subcomponent_matches_any(spec, context.subcomponent_types):
            matched.append(spec)
    return matched


def _matching_metrics(
    context: RecommendationContext,
    metrics: Sequence[str],
    capability_type: str,
) -> List[str]:
    """忽略大小写按 KPI 标准名称过滤指标能力，并保留原始名称。"""
    if capability_type not in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return []
    normalized_kpis = normalized_set(context.kpis)
    if not normalized_kpis:
        return list(metrics)
    return [
        metric
        for metric in metrics
        if normalize_match_value(metric) in normalized_kpis
    ]


def special_candidates(
    context: RecommendationContext,
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成特殊查询候选，并通过设备能力卡解析设备别名。"""
    result = []
    for special_card in special_cards:
        if not _special_card_is_candidate(
            special_card, context, domain_cards, primary_type
        ):
            continue
        result.append(_special_candidate(context, special_card, domain_cards))
    return result


def _special_candidate(
    context: RecommendationContext,
    special_card: SpecialCapabilitySpec,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> CapabilityCandidate:
    """将一张匹配的特殊卡转换为候选能力。"""
    matched_types = _matched_special_device_types(
        context_device_types(context), special_card.device_types, domain_cards
    )
    return CapabilityCandidate(
        capability_id=special_card.capability_id,
        capability_type=special_card.capability_type,
        domain=special_card.domain,
        device_types=matched_types or special_card.device_types,
        objects=special_card.objects,
        properties=special_card.properties,
        table_hints=special_card.table_hints,
        examples=special_card.examples,
        priority=special_card.priority,
    )


def _special_card_is_candidate(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    primary_type: str,
) -> bool:
    """判断特殊卡的类型和上下文是否同时匹配。"""
    if not values_equal(special_card.capability_type, primary_type):
        return False
    return _special_matches_context(special_card, context, domain_cards)


def relation_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[CapabilityCandidate]:
    """在结构化子网或原问题明确关系方向时补充关系候选。"""
    if not _has_relation_direction(context):
        return []
    return special_candidates(context, special_cards, RELATION_QUERY, domain_cards)


def _has_relation_direction(context: RecommendationContext) -> bool:
    """判断上下文是否具有结构化子网或明确关系词。"""
    if context.subnet:
        return True
    return contains_any(context.question, ("下", "相连", "父", "子", "所属"))


def _special_matches_context(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断特殊能力是否与当前设备、对象和问题文本相关。"""
    device_types = context_device_types(context)
    matched_types = _matched_special_device_types(
        device_types, special_card.device_types, domain_cards
    )
    if not _special_device_types_match(special_card, device_types, matched_types):
        return False
    if not _special_objects_match(special_card, context):
        return False
    return _special_query_direction_matches(special_card, context, matched_types)


def _special_query_direction_matches(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    matched_types: Sequence[str],
) -> bool:
    """判断资源或关系特殊能力是否满足额外查询方向。"""
    if values_equal(special_card.capability_type, RESOURCE_QUERY):
        return is_subnet_context(context) or contains_any(
            context.question, special_card.objects
        )
    if values_equal(special_card.capability_type, RELATION_QUERY):
        return _relation_special_matches(special_card, context, matched_types)
    return True


def _special_device_types_match(
    special_card: SpecialCapabilitySpec,
    device_types: Sequence[str],
    matched_device_types: Sequence[str],
) -> bool:
    """判断上下文设备类型是否满足特殊卡约束。"""
    if special_card.device_types and device_types:
        return bool(matched_device_types)
    return True


def _special_objects_match(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
) -> bool:
    """判断上下文子部件是否满足特殊卡对象约束。"""
    if special_card.objects and context.subcomponent_types:
        return has_overlap(special_card.objects, context.subcomponent_types)
    return True


def _relation_special_matches(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    matched_device_types: Sequence[str],
) -> bool:
    """判断关系特殊卡是否具有设备、对象或文本方向。"""
    if matched_device_types:
        return True
    if has_overlap(special_card.objects, context.subcomponent_types):
        return True
    return contains_any(context.question, special_card.objects)


def _matched_special_device_types(
    values: Sequence[str],
    supported: Sequence[str],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """返回能够通过标准类型或设备别名命中特殊能力的原始设备类型。"""
    supported_set = normalized_set(supported)
    matched = []
    for value in values:
        if normalize_match_value(value) in supported_set:
            matched.append(value)
            continue
        if domain_card_alias_supported(value, supported_set, domain_cards):
            matched.append(value)
    return matched


def _slug(values: Sequence[str]) -> str:
    """用首个标准类型生成稳定候选标识片段。"""
    return values[0] if values else "subcomponent"
