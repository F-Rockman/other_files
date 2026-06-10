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


def load_device_capability_profiles() -> List[DeviceCapabilityProfile]:
    """从包内配置加载设备及其嵌套子部件能力规格。"""
    document = _load_capability_document()
    return [
        DeviceCapabilityProfile.from_dict(item)
        for item in document.get("device_profiles", [])
        if isinstance(item, dict)
    ]


def load_special_capabilities() -> List[SpecialCapabilitySpec]:
    """从包内配置加载告警、链路、资源和关系特殊能力。"""
    document = _load_capability_document()
    return [
        SpecialCapabilitySpec.from_dict(item)
        for item in document.get("special_capabilities", [])
        if isinstance(item, dict)
    ]


def resolve_primary_capability_type(context: RecommendationContext) -> str:
    """根据意图、子部件和 count 聚合确定主查询骨架。"""
    if context.intention == "查告警":
        return ALARM_QUERY
    if context.intention == "查链路":
        return LINK_QUERY
    if context.intention == "查信息" and _is_subnet_context(context):
        return RESOURCE_QUERY

    has_subcomponent = bool(context.subcomponent_types)
    is_count = bool(COUNT_AGGREGATIONS.intersection(context.aggregations))
    if context.intention == "查指标":
        return SUBCOMPONENT_METRIC if has_subcomponent else DEVICE_METRIC
    if context.intention == "查信息":
        if has_subcomponent:
            return SUBCOMPONENT_COUNT if is_count else SUBCOMPONENT_INFO
        return DEVICE_COUNT if is_count else DEVICE_INFO
    return ""


def recommend_capabilities(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable] = (),
    profiles: Sequence[DeviceCapabilityProfile] = (),
    special_capabilities: Sequence[SpecialCapabilitySpec] = (),
    limit: int = 12,
) -> List[RankedCapability]:
    """根据标准上下文生成、过滤、排序并选择动态候选能力。"""
    if limit <= 0:
        return []
    available_profiles = list(profiles) if profiles else load_device_capability_profiles()
    available_special = (
        list(special_capabilities) if special_capabilities else load_special_capabilities()
    )
    object_directed_candidates = _empty_intention_basic_candidates(
        context, available_profiles, available_special
    )
    if object_directed_candidates is not None:
        candidates = object_directed_candidates
    else:
        direction_candidates = _recovery_question_direction_candidates(
            context, available_profiles, available_special
        )
        if direction_candidates is not None:
            candidates = direction_candidates
        else:
            matched_profiles = _matching_profiles(context, available_profiles)
            primary_type = resolve_primary_capability_type(context)
            candidates = _primary_candidates(
                context, matched_profiles, available_special, primary_type
            )
            candidates.extend(_adjacent_candidates(context, matched_profiles, primary_type))
    candidates = _dedupe_candidates(candidates)
    if not candidates and context.recovery_strategy == BASIC:
        candidates = _global_basic_fallback_candidates(available_profiles)

    ranked = []
    for candidate in candidates:
        ranked.append(_rank_candidate(context, candidate, metadata_tables))
    ranked.sort(
        key=lambda item: (
            -item.match_score,
            -item.candidate.priority,
            item.candidate.capability_id,
        )
    )
    return _select_diverse(ranked, limit)


def _empty_intention_basic_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
    special_capabilities: Sequence[SpecialCapabilitySpec],
) -> Optional[List[CapabilityCandidate]]:
    """
    按空意图 Basic 原问题中的明确业务对象收敛基础候选。

    返回 ``None`` 表示没有识别到对象方向，应继续使用原有召回流程。
    """
    if context.recovery_strategy != BASIC or context.intention or not context.question:
        return None

    matched_profiles = _profiles_matching_question_direction(context.question, profiles)
    device_terms = _profile_device_terms(profiles)
    matched_device_values = _specific_terms_in_text(context.question, device_terms)
    matched_subcomponents = _subcomponents_matching_text(context.question, profiles)
    matched_special = []
    for spec in special_capabilities:
        if _contains_any(context.question, spec.objects):
            matched_special.append(spec)
    if not matched_profiles and not matched_subcomponents and not matched_special:
        return None

    if matched_special:
        if not matched_device_values:
            matched_device_values = _profile_standard_device_types(matched_profiles)
        special_objects = []
        for spec in matched_special:
            special_objects.extend(spec.objects)
        special_context = RecommendationContext(
            question=context.question,
            devices=_device_conditions_for_types(matched_device_values),
            subcomponent_types=special_objects,
        )
        candidates = []
        for spec in matched_special:
            candidates.extend(
                _special_candidates(
                    special_context, [spec], spec.capability_type, profiles
                )
            )
        if candidates:
            return candidates

    if matched_profiles and matched_subcomponents:
        profile_ids = _profile_ids(matched_profiles)
        filtered_subcomponents = []
        for profile, spec in matched_subcomponents:
            if profile.profile_id in profile_ids:
                filtered_subcomponents.append((profile, spec))
        matched_subcomponents = filtered_subcomponents

    if matched_subcomponents:
        candidates = []
        for profile, spec in matched_subcomponents:
            for capability_type in (SUBCOMPONENT_INFO, SUBCOMPONENT_COUNT):
                candidate = _subcomponent_candidate(
                    context, profile, spec, capability_type, relax=True
                )
                if candidate:
                    candidates.append(candidate)
        return candidates

    candidates = []
    for profile in matched_profiles:
        for capability_type in (DEVICE_INFO, DEVICE_COUNT):
            candidates.extend(
                _profile_candidates(
                    context, profile, capability_type, relax=True
                )
            )
    return candidates


def _recovery_question_direction_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
    special_capabilities: Sequence[SpecialCapabilitySpec],
) -> Optional[List[CapabilityCandidate]]:
    """
    在拒答且无结构化对象时，按原问题中的能力卡领域或对象收敛候选。

    返回 ``None`` 表示问题中没有识别到能力卡已有方向，应继续使用原有全局召回。
    """
    if (
        not context.recovery_strategy
        or _context_device_types(context)
        or context.subcomponent_types
        or not context.question
    ):
        return None

    matched_profiles = _profiles_matching_question_direction(context.question, profiles)
    matched_subcomponents = _subcomponents_matching_text(context.question, profiles)
    if matched_profiles and matched_subcomponents:
        profile_ids = _profile_ids(matched_profiles)
        filtered_subcomponents = []
        for profile, spec in matched_subcomponents:
            if profile.profile_id in profile_ids:
                filtered_subcomponents.append((profile, spec))
        matched_subcomponents = filtered_subcomponents
    elif matched_subcomponents:
        parent_profiles = []
        for profile, _ in matched_subcomponents:
            parent_profiles.append(profile)
        matched_profiles = _dedupe_profiles(parent_profiles)

    if not matched_profiles:
        return None

    device_types = _profile_standard_device_types(matched_profiles)
    subcomponent_types = []
    for _, spec in matched_subcomponents:
        subcomponent_types.extend(spec.types)
    direction_context = replace(
        context,
        devices=_device_conditions_for_types(device_types),
        subcomponent_types=subcomponent_types,
    )
    primary_type = resolve_primary_capability_type(direction_context)
    candidates = _primary_candidates(
        direction_context, matched_profiles, special_capabilities, primary_type
    )
    candidates.extend(
        _adjacent_candidates(direction_context, matched_profiles, primary_type)
    )

    if (
        primary_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC}
        and context.recovery_strategy in KPI_RELAXING_RECOVERY_STRATEGIES
        and not _contains_capability_type(candidates, primary_type)
    ):
        relaxed_context = replace(direction_context, kpis=[])
        candidates.extend(
            _primary_candidates(
                relaxed_context, matched_profiles, special_capabilities, primary_type
            )
        )
    return candidates


def _load_capability_document() -> Dict[str, Any]:
    """读取六类骨架设备规格配置文档。"""
    path = resources.files("question_recommendation").joinpath(
        "data/device_capability_profiles.json"
    )
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    return document if isinstance(document, dict) else {}


def _matching_profiles(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按明确设备类型或子部件对象过滤设备规格。"""
    device_types = _context_device_types(context)
    if device_types:
        matched = []
        for profile in profiles:
            if _profile_matches_any(profile, device_types):
                matched.append(profile)
        return matched
    if context.subcomponent_types:
        matched = []
        for profile in profiles:
            if _profile_has_matching_subcomponent(
                profile, context.subcomponent_types
            ):
                matched.append(profile)
        return matched
    return list(profiles)


def _primary_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
    special_capabilities: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """生成主查询骨架对应的设备或特殊能力候选。"""
    if primary_type in {ALARM_QUERY, LINK_QUERY, RESOURCE_QUERY, RELATION_QUERY}:
        return _special_candidates(context, special_capabilities, primary_type, profiles)
    candidates = []
    for profile in profiles:
        candidates.extend(_profile_candidates(context, profile, primary_type))
    return candidates


def _adjacent_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """在主能力附近补充同对象、低成本且语义不同的候选能力。"""
    if context.subcomponent_types:
        adjacent_types = [SUBCOMPONENT_INFO, SUBCOMPONENT_COUNT]
        if primary_type == SUBCOMPONENT_INFO:
            adjacent_types.append(SUBCOMPONENT_METRIC)
    else:
        adjacent_types = [DEVICE_INFO, DEVICE_COUNT]
        if primary_type == DEVICE_INFO:
            adjacent_types.append(DEVICE_METRIC)

    candidates: List[CapabilityCandidate] = []
    for profile in profiles:
        for capability_type in adjacent_types:
            if capability_type == primary_type:
                continue
            candidates.extend(_profile_candidates(context, profile, capability_type, relax=True))

    if context.intention == "查信息" or context.subnet:
        candidates.extend(_relation_candidates(context, profiles))
    return candidates


def _global_basic_fallback_candidates(
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """在 Basic 没有兼容候选时生成全局设备信息和数量候选。"""
    empty_context = RecommendationContext()
    candidates: List[CapabilityCandidate] = []
    for profile in profiles:
        candidates.extend(
            _profile_candidates(empty_context, profile, DEVICE_INFO, relax=True)
        )
        candidates.extend(
            _profile_candidates(empty_context, profile, DEVICE_COUNT, relax=True)
        )
    return _dedupe_candidates(candidates)


def _profile_candidates(
    context: RecommendationContext,
    profile: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool = False,
) -> List[CapabilityCandidate]:
    """根据一个设备规格和查询骨架动态生成候选能力。"""
    if capability_type in {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}:
        candidate = _device_candidate(context, profile, capability_type, relax)
        return [candidate] if candidate else []

    if capability_type not in {
        SUBCOMPONENT_INFO,
        SUBCOMPONENT_COUNT,
        SUBCOMPONENT_METRIC,
    }:
        return []
    candidates = []
    for spec in _matching_subcomponents(context, profile):
        candidate = _subcomponent_candidate(
            context, profile, spec, capability_type, relax
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _device_candidate(
    context: RecommendationContext,
    profile: DeviceCapabilityProfile,
    capability_type: str,
    relax: bool,
) -> Optional[CapabilityCandidate]:
    """生成设备信息、数量或指标候选。"""
    if not relax and not _locators_compatible(context, profile.locators):
        return None
    metrics = _matching_metrics(context, profile.metrics, capability_type)
    if capability_type == DEVICE_METRIC and not metrics:
        return None

    return CapabilityCandidate(
        capability_id=f"{profile.profile_id}:{capability_type}",
        capability_type=capability_type,
        domain=profile.domain,
        device_types=profile.device_types,
        locators=profile.locators,
        properties=profile.properties if capability_type == DEVICE_INFO else [],
        metrics=metrics,
        table_hints=profile.table_hints,
        examples=_examples_for_type(profile.examples, capability_type),
        priority=profile.priority,
    )


def _subcomponent_candidate(
    context: RecommendationContext,
    profile: DeviceCapabilityProfile,
    spec: SubcomponentCapabilitySpec,
    capability_type: str,
    relax: bool,
) -> Optional[CapabilityCandidate]:
    """生成设备子部件信息、数量或指标候选。"""
    if not relax and not _locators_compatible(context, profile.locators):
        return None
    metrics = _matching_metrics(context, spec.metrics, capability_type)
    if capability_type == SUBCOMPONENT_METRIC and not metrics:
        return None

    return CapabilityCandidate(
        capability_id=f"{profile.profile_id}:{_slug(spec.types)}:{capability_type}",
        capability_type=capability_type,
        domain=profile.domain,
        device_types=profile.device_types,
        subcomponent_types=spec.types,
        locators=profile.locators,
        properties=spec.properties if capability_type == SUBCOMPONENT_INFO else [],
        metrics=metrics,
        table_hints=profile.table_hints + spec.table_hints,
        examples=_examples_for_type(spec.examples, capability_type),
        priority=profile.priority + spec.priority,
    )


def _matching_subcomponents(
    context: RecommendationContext,
    profile: DeviceCapabilityProfile,
) -> List[SubcomponentCapabilitySpec]:
    """返回与上下文对象匹配的嵌套子部件规格。"""
    if not context.subcomponent_types:
        return list(profile.subcomponents)
    matched = []
    for spec in profile.subcomponents:
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
    special_capabilities: Sequence[SpecialCapabilitySpec],
    primary_type: str,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """生成特殊查询候选，并通过设备能力卡解析设备别名。"""
    result = []
    for spec in special_capabilities:
        if not _values_equal(
            spec.capability_type, primary_type
        ) or not _special_matches_context(spec, context, profiles):
            continue
        matched_device_types = _matched_special_device_types(
            _context_device_types(context), spec.device_types, profiles
        )
        result.append(
            CapabilityCandidate(
                capability_id=spec.capability_id,
                capability_type=spec.capability_type,
                domain=spec.domain,
                device_types=matched_device_types or spec.device_types,
                subcomponent_types=spec.objects,
                properties=spec.properties,
                table_hints=spec.table_hints,
                examples=spec.examples,
                priority=spec.priority,
            )
        )
    return result


def _relation_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[CapabilityCandidate]:
    """在结构化子网或原问题明确关系方向时补充关系候选。"""
    if not context.subnet and not _contains_any(
        context.question, ("下", "相连", "父", "子", "所属")
    ):
        return []
    return _special_candidates(
        context, load_special_capabilities(), RELATION_QUERY, profiles
    )


def _special_matches_context(
    spec: SpecialCapabilitySpec,
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断特殊能力是否与当前设备、对象和问题文本相关。"""
    device_types = _context_device_types(context)
    matched_device_types = _matched_special_device_types(
        device_types, spec.device_types, profiles
    )
    if spec.device_types and device_types:
        if not matched_device_types:
            return False
    if spec.objects and context.subcomponent_types:
        if not _has_overlap(spec.objects, context.subcomponent_types):
            return False
    if _values_equal(spec.capability_type, RESOURCE_QUERY):
        return _is_subnet_context(context)
    if _values_equal(spec.capability_type, RELATION_QUERY):
        return bool(
            matched_device_types
            or _has_overlap(spec.objects, context.subcomponent_types)
            or _contains_any(context.question, spec.objects)
        )
    return True


def _matched_special_device_types(
    values: Sequence[str],
    supported: Sequence[str],
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """返回能够通过标准类型或设备能力卡别名命中特殊能力的原始设备类型。"""
    supported_set = _normalized_set(supported)
    matched = []
    for value in values:
        if _normalize_match_value(value) in supported_set:
            matched.append(value)
            continue
        if _profile_alias_supported(value, supported_set, profiles):
            matched.append(value)
    return matched


def _rank_candidate(
    context: RecommendationContext,
    candidate: CapabilityCandidate,
    metadata_tables: Sequence[MetadataTable],
) -> RankedCapability:
    """计算动态候选与上下文的确定性相关分数。"""
    score = candidate.priority
    primary_type = resolve_primary_capability_type(context)
    if _values_equal(candidate.capability_type, primary_type):
        score += 160
    if _has_overlap(_context_device_types(context), candidate.device_types):
        score += 120
    if _has_overlap(context.subcomponent_types, candidate.subcomponent_types):
        score += 100
    if context.kpis and _has_overlap(context.kpis, candidate.metrics):
        score += 60
    if context.properties and _has_overlap(context.properties, candidate.properties):
        score += 40
    if _metadata_matches(candidate.table_hints, context.tables, metadata_tables):
        score += 30
    return RankedCapability(candidate=candidate, match_score=score)


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


def _profile_device_terms(
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型和别名。"""
    terms = []
    for profile in profiles:
        terms.extend(profile.device_types)
        terms.extend(profile.aliases)
    return terms


def _profile_standard_device_types(
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型。"""
    device_types = []
    for profile in profiles:
        device_types.extend(profile.device_types)
    return device_types


def _profile_ids(profiles: Sequence[DeviceCapabilityProfile]) -> set:
    """返回能力卡标识集合。"""
    return {profile.profile_id for profile in profiles}


def _contains_capability_type(
    candidates: Sequence[CapabilityCandidate],
    capability_type: str,
) -> bool:
    """判断候选列表是否包含指定能力骨架。"""
    for candidate in candidates:
        if candidate.capability_type == capability_type:
            return True
    return False


def _profile_matches_any(
    profile: DeviceCapabilityProfile,
    device_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否命中任一设备类型。"""
    for device_type in device_types:
        if profile.matches(device_type):
            return True
    return False


def _profile_has_matching_subcomponent(
    profile: DeviceCapabilityProfile,
    subcomponent_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否包含任一匹配的子部件。"""
    for spec in profile.subcomponents:
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


def _profile_alias_supported(
    value: str,
    supported: set,
    profiles: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断设备别名是否能够映射到特殊能力支持的标准设备类型。"""
    for profile in profiles:
        if not profile.matches(value):
            continue
        profile_types = _normalized_set(profile.device_types)
        if supported.intersection(profile_types):
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
        is_count = _contains_any(example, ("数量", "总数"))
        is_metric = _contains_any(
            example,
            (
                "趋势",
                "平均",
                "最大",
                "最小",
                "Top",
                "利用率",
                "IOPS",
                "响应时间",
                "功率",
                "温度",
                "速率",
                "流量",
                "丢包率",
                "错包率",
                "光功率",
                "不可达比率",
                "当前移动终端数",
            ),
        )
        if capability_type in {DEVICE_COUNT, SUBCOMPONENT_COUNT} and is_count:
            result.append(example)
        elif capability_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC} and is_metric:
            result.append(example)
        elif capability_type in {DEVICE_INFO, SUBCOMPONENT_INFO} and not is_count and not is_metric:
            result.append(example)
    return result


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


def _profiles_matching_text(
    text: str,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按文本中未被更长对象词覆盖的设备类型或别名匹配能力卡。"""
    matched_terms = _normalized_set(
        _specific_terms_in_text(text, _profile_device_terms(profiles))
    )
    matched_profiles = []
    for profile in profiles:
        profile_terms = _normalized_set(profile.device_types + profile.aliases)
        if matched_terms.intersection(profile_terms):
            matched_profiles.append(profile)
    return matched_profiles


def _profiles_matching_question_direction(
    text: str,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按原问题中能力卡已有的业务域、设备类型或别名匹配设备规格。"""
    object_profile_ids = _profile_ids(_profiles_matching_text(text, profiles))
    profile_domains = [profile.domain for profile in profiles]
    matched_domains = _normalized_set(
        _specific_terms_in_text(text, profile_domains)
    )
    matched_profiles = []
    for profile in profiles:
        if profile.profile_id in object_profile_ids:
            matched_profiles.append(profile)
            continue
        if _normalize_match_value(profile.domain) in matched_domains:
            matched_profiles.append(profile)
    return matched_profiles


def _subcomponents_matching_text(
    text: str,
    profiles: Sequence[DeviceCapabilityProfile],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """按原问题中能力卡已有的子部件类型或别名匹配父设备与子部件规格。"""
    subcomponent_terms = []
    for profile in profiles:
        for spec in profile.subcomponents:
            subcomponent_terms.extend(spec.types)
            subcomponent_terms.extend(spec.aliases)
    matched_terms = _normalized_set(
        _specific_terms_in_text(text, subcomponent_terms)
    )
    matched_subcomponents = []
    for profile in profiles:
        for spec in profile.subcomponents:
            spec_terms = _normalized_set(spec.types + spec.aliases)
            if matched_terms.intersection(spec_terms):
                matched_subcomponents.append((profile, spec))
    return matched_subcomponents


def _dedupe_profiles(
    profiles: Iterable[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按能力卡标识去重并保留首次出现的设备规格。"""
    result: List[DeviceCapabilityProfile] = []
    seen = set()
    for profile in profiles:
        if profile.profile_id and profile.profile_id not in seen:
            seen.add(profile.profile_id)
            result.append(profile)
    return result


def _specific_terms_in_text(text: str, terms: Sequence[str]) -> List[str]:
    """忽略大小写返回明确对象词，并移除被更长对象词完整覆盖的短词。"""
    normalized_text = _normalize_match_value(text)
    matches: List[Tuple[str, int, int]] = []
    unique_terms = {}
    for term in terms:
        normalized_term = _normalize_match_value(term)
        if normalized_term and normalized_term not in unique_terms:
            unique_terms[normalized_term] = term
    for normalized_term, term in unique_terms.items():
        start = normalized_text.find(normalized_term)
        while start >= 0:
            matches.append((term, start, start + len(normalized_term)))
            start = normalized_text.find(normalized_term, start + 1)

    result: List[str] = []
    for term, start, end in matches:
        if _is_covered_by_longer_term(term, start, end, matches):
            continue
        if term not in result:
            result.append(term)
    return result


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
