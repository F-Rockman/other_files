"""六类查询骨架、设备能力规格和特殊能力的确定性召回算法。"""

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    CapabilityCandidate,
    DeviceCapabilityProfile,
    MetadataTable,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)
from .refusal_rules import BASIC


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
    matched_profiles = _matching_profiles(context, available_profiles)

    primary_type = resolve_primary_capability_type(context)
    candidates = _primary_candidates(
        context, matched_profiles, available_special, primary_type
    )
    candidates.extend(_adjacent_candidates(context, matched_profiles, primary_type))
    candidates = _dedupe_candidates(candidates)
    if not candidates and context.recovery_strategy == BASIC:
        candidates = _global_basic_fallback_candidates(available_profiles)

    ranked = [
        _rank_candidate(context, candidate, metadata_tables)
        for candidate in candidates
    ]
    ranked.sort(
        key=lambda item: (
            -item.match_score,
            -item.candidate.priority,
            item.candidate.capability_id,
        )
    )
    return _select_diverse(ranked, limit)


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
    if context.device_types:
        return [
            profile
            for profile in profiles
            if any(profile.matches(item) for item in context.device_types)
        ]
    if context.subcomponent_types:
        return [
            profile
            for profile in profiles
            if any(
                spec.matches(item)
                for spec in profile.subcomponents
                for item in context.subcomponent_types
            )
        ]
    return list(profiles)


def _primary_candidates(
    context: RecommendationContext,
    profiles: Sequence[DeviceCapabilityProfile],
    special_capabilities: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """生成主查询骨架对应的设备或特殊能力候选。"""
    if primary_type in {ALARM_QUERY, LINK_QUERY, RESOURCE_QUERY, RELATION_QUERY}:
        return _special_candidates(context, special_capabilities, primary_type)
    return [
        candidate
        for profile in profiles
        for candidate in _profile_candidates(context, profile, primary_type)
    ]


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

    if context.intention == "查信息":
        candidates.extend(_relation_candidates(context))
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
    return [
        candidate
        for spec in _matching_subcomponents(context, profile)
        for candidate in [_subcomponent_candidate(context, profile, spec, capability_type, relax)]
        if candidate
    ]


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
    return [
        spec
        for spec in profile.subcomponents
        if any(spec.matches(item) for item in context.subcomponent_types)
    ]


def _matching_metrics(
    context: RecommendationContext,
    metrics: Sequence[str],
    capability_type: str,
) -> List[str]:
    """按 KPI 标准名称过滤指标能力。"""
    if capability_type not in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return []
    return [
        metric
        for metric in metrics
        if not context.kpis or metric in context.kpis
    ]


def _special_candidates(
    context: RecommendationContext,
    special_capabilities: Sequence[SpecialCapabilitySpec],
    primary_type: str,
) -> List[CapabilityCandidate]:
    """生成当前特殊查询类型允许的候选能力。"""
    result = []
    for spec in special_capabilities:
        if spec.capability_type != primary_type or not _special_matches_context(spec, context):
            continue
        result.append(
            CapabilityCandidate(
                capability_id=spec.capability_id,
                capability_type=spec.capability_type,
                domain=spec.domain,
                device_types=_matched_values(context.device_types, spec.device_types)
                or spec.device_types,
                subcomponent_types=spec.objects,
                properties=spec.properties,
                table_hints=spec.table_hints,
                examples=spec.examples,
                priority=spec.priority,
            )
        )
    return result


def _relation_candidates(context: RecommendationContext) -> List[CapabilityCandidate]:
    """普通信息场景下仅在原问题明确关系方向时补充关系候选。"""
    text = context.question
    if not any(word in text for word in ("下", "相连", "父", "子", "所属")):
        return []
    return _special_candidates(context, load_special_capabilities(), RELATION_QUERY)


def _special_matches_context(
    spec: SpecialCapabilitySpec,
    context: RecommendationContext,
) -> bool:
    """判断特殊能力是否与当前设备、对象和问题文本相关。"""
    if spec.device_types and context.device_types:
        if not set(spec.device_types).intersection(context.device_types):
            return False
    if spec.objects and context.subcomponent_types:
        if not set(spec.objects).intersection(context.subcomponent_types):
            return False
    if spec.capability_type == RESOURCE_QUERY:
        return _is_subnet_context(context)
    if spec.capability_type == RELATION_QUERY:
        words = set(spec.device_types + spec.objects)
        return bool(words.intersection(context.device_types + context.subcomponent_types)) or any(
            word and word in context.question for word in words
        )
    return True


def _rank_candidate(
    context: RecommendationContext,
    candidate: CapabilityCandidate,
    metadata_tables: Sequence[MetadataTable],
) -> RankedCapability:
    """计算动态候选与上下文的确定性相关分数。"""
    score = candidate.priority
    primary_type = resolve_primary_capability_type(context)
    if candidate.capability_type == primary_type:
        score += 160
    if set(context.device_types).intersection(candidate.device_types):
        score += 120
    if set(context.subcomponent_types).intersection(candidate.subcomponent_types):
        score += 100
    if context.kpis and set(context.kpis).intersection(candidate.metrics):
        score += 60
    if context.properties and set(context.properties).intersection(candidate.properties):
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
    flattened = " ".join(
        list(table_names)
        + [
            text
            for table in metadata_tables
            for text in (table.table_name, table.table_description)
            if text
        ]
        + [
            text
            for table in metadata_tables
            for column in table.columns
            for text in (column.column_name, column.column_description)
            if text
        ]
    )
    return any(hint and hint in flattened for hint in hints)


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
    """判断仍有效的定位参数是否被设备规格支持。"""
    identifier_types = {item.id_type for item in context.identifiers}
    return not identifier_types or bool(identifier_types.intersection(locators))


def _is_subnet_context(context: RecommendationContext) -> bool:
    """判断上下文是否明确查询子网资源。"""
    return "子网" in context.device_types or "子网" in context.subcomponent_types


def _examples_for_type(examples: Sequence[str], capability_type: str) -> List[str]:
    """只保留与当前六类骨架一致的表达示例，避免 Basic 被指标示例干扰。"""
    result = []
    for example in examples:
        is_count = any(word in example for word in ("数量", "总数"))
        is_metric = any(
            word in example
            for word in (
                "趋势",
                "平均",
                "最大",
                "最小",
                "Top",
                "TOP",
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
            )
        )
        if capability_type in {DEVICE_COUNT, SUBCOMPONENT_COUNT} and is_count:
            result.append(example)
        elif capability_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC} and is_metric:
            result.append(example)
        elif capability_type in {DEVICE_INFO, SUBCOMPONENT_INFO} and not is_count and not is_metric:
            result.append(example)
    return result


def _matched_values(values: Sequence[str], supported: Sequence[str]) -> List[str]:
    """按原顺序返回输入和支持集合的交集。"""
    return [item for item in values if item in supported]


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
