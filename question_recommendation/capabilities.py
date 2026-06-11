"""六类查询骨架、设备能力规格和特殊能力的确定性召回算法。"""

import json
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    CapabilityCandidate,
    DeviceCondition,
    DeviceCapabilityProfile,
    MetadataTable,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)
from .refusal_rules import BASIC, CLARIFY, DISAMBIGUATE


DEVICE_INFO = "device_info"
DEVICE_COUNT = "device_count"
DEVICE_METRIC = "device_metric"
SUBCOMPONENT_INFO = "subcomponent_info"
SUBCOMPONENT_COUNT = "subcomponent_count"
SUBCOMPONENT_METRIC = "subcomponent_metric"

ALARM_QUERY = "alarm_query"
LINK_QUERY = "link_query"
RESOURCE_QUERY = "resource_query"
RELATION_QUERY = "relation_query"

COUNT_AGGREGATIONS = {"count", "count_distinct"}
KPI_RELAXING_RECOVERY_STRATEGIES = {CLARIFY, DISAMBIGUATE}


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


def load_capability_cards() -> Tuple[
    List[DeviceCapabilityProfile],
    List[SpecialCapabilitySpec],
]:
    """一次读取包内配置，同时加载领域卡和特殊卡。"""
    document = _load_capability_document()
    domain_cards = [
        DeviceCapabilityProfile.from_dict(item)
        for item in document.get("device_profiles", [])
        if isinstance(item, dict)
    ]
    special_cards = [
        SpecialCapabilitySpec.from_dict(item)
        for item in document.get("special_capabilities", [])
        if isinstance(item, dict)
    ]
    return domain_cards, special_cards


def resolve_primary_capability_type(context: RecommendationContext) -> str:
    """根据意图、子部件和 count 聚合确定主查询骨架。"""
    special_type = _special_primary_capability_type(context)
    if special_type:
        return special_type
    if context.intention == "查指标":
        if context.subcomponent_types:
            return SUBCOMPONENT_METRIC
        return DEVICE_METRIC
    if context.intention == "查信息":
        return _information_primary_capability_type(context)
    return ""


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
    candidates = _recall_candidates(
        context, resolved_domain_cards, resolved_special_cards
    )
    candidates = _dedupe_candidates(candidates)
    if not candidates and context.recovery_strategy == BASIC:
        candidates = _global_basic_fallback_candidates(resolved_domain_cards)
    ranked = _rank_candidates(context, candidates, metadata_tables)
    return _select_diverse(ranked, limit)


def _special_primary_capability_type(context: RecommendationContext) -> str:
    """解析告警、链路和子网资源等特殊主能力。"""
    if context.intention == "查告警":
        return ALARM_QUERY
    if context.intention == "查链路":
        return LINK_QUERY
    if context.intention == "查信息" and _is_subnet_context(context):
        return RESOURCE_QUERY
    return ""


def _information_primary_capability_type(context: RecommendationContext) -> str:
    """解析信息意图下的设备或子部件信息、数量骨架。"""
    is_count = bool(COUNT_AGGREGATIONS.intersection(context.aggregations))
    if context.subcomponent_types:
        if is_count:
            return SUBCOMPONENT_COUNT
        return SUBCOMPONENT_INFO
    if is_count:
        return DEVICE_COUNT
    return DEVICE_INFO


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


def _recall_candidates(
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
    matched_domain_cards = _matching_domain_cards(context, domain_cards)
    primary_type = resolve_primary_capability_type(context)
    candidates = _primary_candidates(
        context, matched_domain_cards, special_cards, primary_type
    )
    candidates.extend(
        _adjacent_candidates(
            context, matched_domain_cards, special_cards, primary_type
        )
    )
    return candidates


def _rank_candidates(
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


def _empty_intention_basic_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> Optional[List[CapabilityCandidate]]:
    """按空意图 Basic 原问题中的明确业务对象收敛基础候选。"""
    if not _uses_empty_intention_basic_direction(context):
        return None

    matched_domain_cards = _domain_cards_matching_question_direction(
        context.question, domain_cards
    )
    matched_device_values = _matched_device_terms(context.question, domain_cards)
    matched_subcomponents = _subcomponents_matching_text(
        context.question, domain_cards
    )
    matched_special_cards = _special_cards_matching_text(
        context.question, special_cards
    )
    if not matched_domain_cards and not matched_subcomponents and not matched_special_cards:
        return None

    special_candidates = _basic_special_candidates(
        context,
        matched_domain_cards,
        matched_device_values,
        matched_special_cards,
        domain_cards,
    )
    if special_candidates:
        return special_candidates

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


def _matched_device_terms(
    question: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """返回原问题中明确出现的设备类型或别名。"""
    return _specific_terms_in_text(
        question, _domain_card_device_terms(domain_cards)
    )


def _special_cards_matching_text(
    text: str,
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[SpecialCapabilitySpec]:
    """返回对象词出现在原问题中的特殊卡。"""
    matched = []
    for special_card in special_cards:
        if _contains_any(text, special_card.objects):
            matched.append(special_card)
    return matched


def _basic_special_candidates(
    context: RecommendationContext,
    matched_domain_cards: Sequence[DeviceCapabilityProfile],
    matched_device_values: Sequence[str],
    matched_special_cards: Sequence[SpecialCapabilitySpec],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成空意图 Basic 的特殊能力候选。"""
    if not matched_special_cards:
        return []
    device_values = list(matched_device_values)
    if not device_values:
        device_values = _domain_card_standard_device_types(matched_domain_cards)
    special_context = _basic_special_context(
        context.question, device_values, matched_special_cards
    )
    candidates = []
    for special_card in matched_special_cards:
        candidates.extend(
            _special_candidates(
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
        devices=_device_conditions_for_types(device_types),
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
    domain_card_ids = _domain_card_ids(matched_domain_cards)
    result = []
    for domain_card, subcomponent_card in matched_subcomponents:
        if domain_card.profile_id in domain_card_ids:
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
            candidate = _subcomponent_candidate(
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
                _domain_card_candidates(
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
    primary_type = resolve_primary_capability_type(direction_context)
    candidates = _primary_candidates(
        direction_context, matched_domain_cards, special_cards, primary_type
    )
    candidates.extend(
        _adjacent_candidates(
            direction_context, matched_domain_cards, special_cards, primary_type
        )
    )
    _append_relaxed_metric_candidates(
        candidates,
        context,
        direction_context,
        matched_domain_cards,
        special_cards,
        primary_type,
    )
    return candidates


def _uses_recovery_question_direction(context: RecommendationContext) -> bool:
    """判断拒答场景是否需要从原问题补充业务方向。"""
    return bool(
        context.recovery_strategy
        and not _context_device_types(context)
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
    matched_domain_cards = _domain_cards_matching_question_direction(
        question, domain_cards
    )
    matched_subcomponents = _subcomponents_matching_text(question, domain_cards)
    if matched_domain_cards:
        matched_subcomponents = _constrain_subcomponent_matches(
            matched_domain_cards, matched_subcomponents
        )
    elif matched_subcomponents:
        parent_cards = []
        for domain_card, _ in matched_subcomponents:
            parent_cards.append(domain_card)
        matched_domain_cards = _dedupe_domain_cards(parent_cards)
    return matched_domain_cards, matched_subcomponents


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
        devices=_device_conditions_for_types(
            _domain_card_standard_device_types(matched_domain_cards)
        ),
        subcomponent_types=subcomponent_types,
    )


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
    if _contains_capability_type(candidates, primary_type):
        return
    relaxed_context = replace(direction_context, kpis=[])
    candidates.extend(
        _primary_candidates(
            relaxed_context, domain_cards, special_cards, primary_type
        )
    )


def _load_capability_document() -> Dict[str, Any]:
    """读取六类骨架设备规格配置文档。"""
    path = resources.files("question_recommendation").joinpath(
        "data/device_capability_profiles.json"
    )
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    return document if isinstance(document, dict) else {}


def _matching_domain_cards(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按明确设备类型或子部件对象过滤设备规格。"""
    device_types = _context_device_types(context)
    if device_types:
        matched = []
        for domain_card in domain_cards:
            if _domain_card_matches_any(domain_card, device_types):
                matched.append(domain_card)
        return matched
    if context.subcomponent_types:
        matched = []
        for domain_card in domain_cards:
            if _domain_card_has_matching_subcomponent(
                domain_card, context.subcomponent_types
            ):
                matched.append(domain_card)
        return matched
    return list(domain_cards)


def _primary_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """生成主查询骨架对应的设备或特殊能力候选。"""
    if primary_type in {ALARM_QUERY, LINK_QUERY, RESOURCE_QUERY, RELATION_QUERY}:
        return _special_candidates(context, special_cards, primary_type, domain_cards)
    candidates = []
    for domain_card in domain_cards:
        candidates.extend(
            _domain_card_candidates(context, domain_card, primary_type)
        )
    return candidates


def _adjacent_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """在主能力附近补充同对象、低成本且语义不同的候选能力。"""
    adjacent_types = _adjacent_capability_types(context, primary_type)
    candidates: List[CapabilityCandidate] = []
    for domain_card in domain_cards:
        for capability_type in adjacent_types:
            if capability_type == primary_type:
                continue
            candidates.extend(
                _domain_card_candidates(
                    context, domain_card, capability_type, relax=True
                )
            )

    if context.intention == "查信息" or context.subnet:
        candidates.extend(
            _relation_candidates(context, domain_cards, special_cards)
        )
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


def _global_basic_fallback_candidates(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """在 Basic 没有兼容候选时生成全局设备信息和数量候选。"""
    empty_context = RecommendationContext()
    candidates: List[CapabilityCandidate] = []
    for domain_card in domain_cards:
        candidates.extend(
            _domain_card_candidates(
                empty_context, domain_card, DEVICE_INFO, relax=True
            )
        )
        candidates.extend(
            _domain_card_candidates(
                empty_context, domain_card, DEVICE_COUNT, relax=True
            )
        )
    return _dedupe_candidates(candidates)


def _domain_card_candidates(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool = False,
) -> List[CapabilityCandidate]:
    """根据一个设备规格和查询骨架动态生成候选能力。"""
    if capability_type in {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}:
        candidate = _device_candidate(
            context, domain_card, capability_type, relax
        )
        return [candidate] if candidate else []

    if capability_type not in {
        SUBCOMPONENT_INFO,
        SUBCOMPONENT_COUNT,
        SUBCOMPONENT_METRIC,
    }:
        return []
    candidates = []
    for spec in _matching_subcomponents(context, domain_card):
        candidate = _subcomponent_candidate(
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
    if not relax and not _locators_compatible(context, domain_card.locators):
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
        examples=_examples_for_type(domain_card.examples, capability_type),
        priority=domain_card.priority,
    )


def _subcomponent_candidate(
    context: RecommendationContext,
    domain_card: DeviceCapabilityProfile,
    spec: SubcomponentCapabilitySpec,
    capability_type: str,
    relax: bool,
) -> Optional[CapabilityCandidate]:
    """生成设备子部件信息、数量或指标候选。"""
    if not relax and not _locators_compatible(context, domain_card.locators):
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
        examples=_examples_for_type(spec.examples, capability_type),
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
        if _subcomponent_matches_any(spec, context.subcomponent_types):
            matched.append(spec)
    return matched


def _matching_metrics(
    context: RecommendationContext,
    metrics: Sequence[str],
    capability_type: str,
) -> List[str]:
    """忽略大小写按 KPI 标准名称过滤指标能力，并保留能力卡原始名称。"""
    if capability_type not in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return []
    normalized_kpis = _normalized_set(context.kpis)
    if not normalized_kpis:
        return list(metrics)
    return [
        metric
        for metric in metrics
        if _normalize_match_value(metric) in normalized_kpis
    ]


def _special_candidates(
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
        matched_device_types = _matched_special_device_types(
            _context_device_types(context),
            special_card.device_types,
            domain_cards,
        )
        result.append(
            CapabilityCandidate(
                capability_id=special_card.capability_id,
                capability_type=special_card.capability_type,
                domain=special_card.domain,
                device_types=matched_device_types or special_card.device_types,
                subcomponent_types=special_card.objects,
                properties=special_card.properties,
                table_hints=special_card.table_hints,
                examples=special_card.examples,
                priority=special_card.priority,
            )
        )
    return result


def _special_card_is_candidate(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    primary_type: str,
) -> bool:
    """判断特殊卡的类型和上下文是否同时匹配。"""
    if not _values_equal(special_card.capability_type, primary_type):
        return False
    return _special_matches_context(special_card, context, domain_cards)


def _relation_candidates(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
    special_cards: Sequence[SpecialCapabilitySpec],
) -> List[CapabilityCandidate]:
    """在结构化子网或原问题明确关系方向时补充关系候选。"""
    if not _has_relation_direction(context):
        return []
    return _special_candidates(
        context, special_cards, RELATION_QUERY, domain_cards
    )


def _has_relation_direction(context: RecommendationContext) -> bool:
    """判断上下文是否具有结构化子网或明确关系词。"""
    if context.subnet:
        return True
    return _contains_any(context.question, ("下", "相连", "父", "子", "所属"))


def _special_matches_context(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断特殊能力是否与当前设备、对象和问题文本相关。"""
    device_types = _context_device_types(context)
    matched_device_types = _matched_special_device_types(
        device_types, special_card.device_types, domain_cards
    )
    if not _special_device_types_match(
        special_card, device_types, matched_device_types
    ):
        return False
    if not _special_objects_match(special_card, context):
        return False
    if _values_equal(special_card.capability_type, RESOURCE_QUERY):
        return _is_subnet_context(context)
    if _values_equal(special_card.capability_type, RELATION_QUERY):
        return _relation_special_matches(
            special_card, context, matched_device_types
        )
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
        return _has_overlap(special_card.objects, context.subcomponent_types)
    return True


def _relation_special_matches(
    special_card: SpecialCapabilitySpec,
    context: RecommendationContext,
    matched_device_types: Sequence[str],
) -> bool:
    """判断关系特殊卡是否具有设备、对象或文本方向。"""
    if matched_device_types:
        return True
    if _has_overlap(special_card.objects, context.subcomponent_types):
        return True
    return _contains_any(context.question, special_card.objects)


def _matched_special_device_types(
    values: Sequence[str],
    supported: Sequence[str],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """返回能够通过标准类型或设备能力卡别名命中特殊能力的原始设备类型。"""
    supported_set = _normalized_set(supported)
    matched = []
    for value in values:
        if _normalize_match_value(value) in supported_set:
            matched.append(value)
            continue
        if _domain_card_alias_supported(value, supported_set, domain_cards):
            matched.append(value)
    return matched


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
    if _values_equal(candidate.capability_type, resolve_primary_capability_type(context)):
        score += 160
    if _has_overlap(_context_device_types(context), candidate.device_types):
        score += 120
    if _has_overlap(context.subcomponent_types, candidate.subcomponent_types):
        score += 100
    if context.kpis and _has_overlap(context.kpis, candidate.metrics):
        score += 60
    if context.properties and _has_overlap(context.properties, candidate.properties):
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
    flattened = _normalize_match_value(" ".join(metadata_texts))
    for hint in hints:
        normalized_hint = _normalize_match_value(hint)
        if normalized_hint and normalized_hint in flattened:
            return True
    return False


def _select_diverse(
    ranked: Sequence[RankedCapability],
    limit: int,
) -> List[RankedCapability]:
    """按能力骨架和对象族限制重复，选择稳定且有差异的 Top N。"""
    selected: List[RankedCapability] = []
    group_counts: Dict[Tuple[str, str, str], int] = {}
    for item in ranked:
        candidate = item.candidate
        key = (
            candidate.capability_type,
            candidate.device_types[0] if candidate.device_types else "",
            candidate.subcomponent_types[0] if candidate.subcomponent_types else "",
        )
        if group_counts.get(key, 0) >= 2:
            continue
        selected.append(item)
        group_counts[key] = group_counts.get(key, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def _locators_compatible(context: RecommendationContext, locators: Sequence[str]) -> bool:
    """忽略大小写判断仍有效的定位参数是否被设备规格支持。"""
    identifier_types = []
    for item in context.devices:
        if item.device_id:
            identifier_types.append(item.id_type)
    normalized_identifier_types = _normalized_set(identifier_types)
    return not normalized_identifier_types or bool(
        normalized_identifier_types.intersection(_normalized_set(locators))
    )


def _is_subnet_context(context: RecommendationContext) -> bool:
    """判断上下文是否明确查询子网资源。"""
    return _normalize_match_value("子网") in _normalized_set(
        _context_device_types(context) + context.subcomponent_types
    )


def _context_device_types(context: RecommendationContext) -> List[str]:
    """从设备条件实时派生去重后的原始设备类型，并保持首次出现顺序。"""
    result: List[str] = []
    seen = set()
    for condition in context.devices:
        device_type = str(condition.device_type or "").strip()
        normalized = _normalize_match_value(device_type)
        if device_type and normalized not in seen:
            seen.add(normalized)
            result.append(device_type)
    return result


def _device_conditions_for_types(device_types: Iterable[str]) -> List[DeviceCondition]:
    """将内部识别出的设备类型方向转换为不带定位值的设备条件。"""
    result: List[DeviceCondition] = []
    seen = set()
    for device_type in device_types:
        text = str(device_type or "").strip()
        normalized = _normalize_match_value(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(DeviceCondition(device_type=text))
    return result


def _domain_card_device_terms(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型和别名。"""
    terms = []
    for domain_card in domain_cards:
        terms.extend(domain_card.device_types)
        terms.extend(domain_card.aliases)
    return terms


def _domain_card_standard_device_types(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型。"""
    device_types = []
    for domain_card in domain_cards:
        device_types.extend(domain_card.device_types)
    return device_types


def _domain_card_ids(domain_cards: Sequence[DeviceCapabilityProfile]) -> set:
    """返回能力卡标识集合。"""
    return {domain_card.profile_id for domain_card in domain_cards}


def _contains_capability_type(
    candidates: Sequence[CapabilityCandidate],
    capability_type: str,
) -> bool:
    """判断候选列表是否包含指定能力骨架。"""
    for candidate in candidates:
        if candidate.capability_type == capability_type:
            return True
    return False


def _domain_card_matches_any(
    domain_card: DeviceCapabilityProfile,
    device_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否命中任一设备类型。"""
    for device_type in device_types:
        if domain_card.matches(device_type):
            return True
    return False


def _domain_card_has_matching_subcomponent(
    domain_card: DeviceCapabilityProfile,
    subcomponent_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否包含任一匹配的子部件。"""
    for spec in domain_card.subcomponents:
        if _subcomponent_matches_any(spec, subcomponent_types):
            return True
    return False


def _subcomponent_matches_any(
    spec: SubcomponentCapabilitySpec,
    subcomponent_types: Sequence[str],
) -> bool:
    """判断子部件能力是否命中任一子部件类型。"""
    for subcomponent_type in subcomponent_types:
        if spec.matches(subcomponent_type):
            return True
    return False


def _domain_card_alias_supported(
    value: str,
    supported: set,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断设备别名是否能够映射到特殊能力支持的标准设备类型。"""
    for domain_card in domain_cards:
        if not domain_card.matches(value):
            continue
        domain_card_types = _normalized_set(domain_card.device_types)
        if supported.intersection(domain_card_types):
            return True
    return False


def _append_nonempty(values: List[str], value: str) -> None:
    """将非空文本追加到元数据匹配文本列表。"""
    if value:
        values.append(value)


def _examples_for_type(examples: Sequence[str], capability_type: str) -> List[str]:
    """只保留与当前六类骨架一致的表达示例，避免 Basic 被指标示例干扰。"""
    result = []
    for example in examples:
        if _example_matches_type(example, capability_type):
            result.append(example)
    return result


def _example_matches_type(example: str, capability_type: str) -> bool:
    """判断自然问法示例是否与指定能力骨架一致。"""
    is_count = _contains_any(example, ("数量", "总数"))
    is_metric = _is_metric_example(example)
    if capability_type in {DEVICE_COUNT, SUBCOMPONENT_COUNT}:
        return is_count
    if capability_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return is_metric
    if capability_type in {DEVICE_INFO, SUBCOMPONENT_INFO}:
        return not is_count and not is_metric
    return False


def _is_metric_example(example: str) -> bool:
    """判断示例是否表达指标、趋势、聚合或排名方向。"""
    metric_terms = (
        "趋势", "平均", "最大", "最小", "Top", "利用率", "IOPS",
        "响应时间", "功率", "温度", "速率", "流量", "丢包率", "错包率",
        "光功率", "不可达比率", "当前移动终端数",
    )
    return _contains_any(example, metric_terms)


def _dedupe_candidates(
    candidates: Iterable[CapabilityCandidate],
) -> List[CapabilityCandidate]:
    """按候选能力标识去重并保留首次出现项。"""
    result: List[CapabilityCandidate] = []
    seen = set()
    for candidate in candidates:
        if candidate.capability_id and candidate.capability_id not in seen:
            seen.add(candidate.capability_id)
            result.append(candidate)
    return result


def _slug(values: Sequence[str]) -> str:
    """用首个标准类型生成稳定候选标识片段。"""
    return values[0] if values else "subcomponent"


def _contains_any(text: str, values: Sequence[str]) -> bool:
    """忽略大小写判断文本是否包含任一非空能力卡字段值。"""
    normalized_text = _normalize_match_value(text)
    for value in values:
        normalized_value = _normalize_match_value(value)
        if normalized_value and normalized_value in normalized_text:
            return True
    return False


def _domain_cards_matching_text(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按文本中未被更长对象词覆盖的设备类型或别名匹配能力卡。"""
    matched_terms = _normalized_set(
        _specific_terms_in_text(text, _domain_card_device_terms(domain_cards))
    )
    matched_domain_cards = []
    for domain_card in domain_cards:
        card_terms = _normalized_set(domain_card.device_types + domain_card.aliases)
        if matched_terms.intersection(card_terms):
            matched_domain_cards.append(domain_card)
    return matched_domain_cards


def _domain_cards_matching_question_direction(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按原问题中能力卡已有的业务域、设备类型或别名匹配设备规格。"""
    object_card_ids = _domain_card_ids(
        _domain_cards_matching_text(text, domain_cards)
    )
    domains = [domain_card.domain for domain_card in domain_cards]
    matched_domains = _normalized_set(
        _specific_terms_in_text(text, domains)
    )
    matched_domain_cards = []
    for domain_card in domain_cards:
        if domain_card.profile_id in object_card_ids:
            matched_domain_cards.append(domain_card)
            continue
        if _normalize_match_value(domain_card.domain) in matched_domains:
            matched_domain_cards.append(domain_card)
    return matched_domain_cards


def _subcomponents_matching_text(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """按原问题中能力卡已有的子部件类型或别名匹配父设备与子部件规格。"""
    subcomponent_terms = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            subcomponent_terms.extend(spec.types)
            subcomponent_terms.extend(spec.aliases)
    matched_terms = _normalized_set(
        _specific_terms_in_text(text, subcomponent_terms)
    )
    matched_subcomponents = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            spec_terms = _normalized_set(spec.types + spec.aliases)
            if matched_terms.intersection(spec_terms):
                matched_subcomponents.append((domain_card, spec))
    return matched_subcomponents


def _dedupe_domain_cards(
    domain_cards: Iterable[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按能力卡标识去重并保留首次出现的设备规格。"""
    result: List[DeviceCapabilityProfile] = []
    seen = set()
    for domain_card in domain_cards:
        if domain_card.profile_id and domain_card.profile_id not in seen:
            seen.add(domain_card.profile_id)
            result.append(domain_card)
    return result


def _specific_terms_in_text(text: str, terms: Sequence[str]) -> List[str]:
    """忽略大小写返回明确对象词，并移除被更长对象词完整覆盖的短词。"""
    normalized_text = _normalize_match_value(text)
    matches = _term_occurrences(normalized_text, terms)
    result: List[str] = []
    for term, start, end in matches:
        if _is_covered_by_longer_term(term, start, end, matches):
            continue
        if term not in result:
            result.append(term)
    return result


def _term_occurrences(
    normalized_text: str,
    terms: Sequence[str],
) -> List[Tuple[str, int, int]]:
    """返回去重词项在规范文本中的全部出现位置。"""
    matches: List[Tuple[str, int, int]] = []
    for normalized_term, term in _unique_normalized_terms(terms).items():
        matches.extend(
            _single_term_occurrences(normalized_text, normalized_term, term)
        )
    return matches


def _unique_normalized_terms(terms: Sequence[str]) -> Dict[str, str]:
    """按首次出现顺序返回规范词项到展示词项的映射。"""
    unique_terms = {}
    for term in terms:
        normalized_term = _normalize_match_value(term)
        if normalized_term and normalized_term not in unique_terms:
            unique_terms[normalized_term] = term
    return unique_terms


def _single_term_occurrences(
    normalized_text: str,
    normalized_term: str,
    term: str,
) -> List[Tuple[str, int, int]]:
    """返回一个规范词项在文本中的全部出现位置。"""
    matches = []
    start = normalized_text.find(normalized_term)
    while start >= 0:
        matches.append((term, start, start + len(normalized_term)))
        start = normalized_text.find(normalized_term, start + 1)
    return matches


def _is_covered_by_longer_term(
    term: str,
    start: int,
    end: int,
    matches: Sequence[Tuple[str, int, int]],
) -> bool:
    """判断对象词是否被同位置范围内更长的对象词完整覆盖。"""
    for other, other_start, other_end in matches:
        if other_start > start or other_end < end:
            continue
        if len(other) > len(term):
            return True
    return False


def _normalize_match_value(value: Any) -> str:
    """规范能力卡匹配文本，忽略首尾空白与大小写但保留原始展示值。"""
    return str(value or "").strip().casefold()


def _normalized_set(values: Iterable[Any]) -> set:
    """返回去除空值并忽略大小写的能力卡字段集合。"""
    result = set()
    for value in values:
        normalized = _normalize_match_value(value)
        if normalized:
            result.add(normalized)
    return result


def _has_overlap(left: Iterable[Any], right: Iterable[Any]) -> bool:
    """忽略大小写判断两组能力卡字段值是否存在交集。"""
    return bool(_normalized_set(left).intersection(_normalized_set(right)))


def _values_equal(left: Any, right: Any) -> bool:
    """忽略大小写判断两个能力卡字段值是否相等。"""
    return _normalize_match_value(left) == _normalize_match_value(right)
