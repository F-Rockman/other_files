"""内置能力卡加载、硬过滤、确定性打分和 Top N 选择。"""

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .models import CapabilityCard, MetadataTable, RecommendationContext
from .refusal_rules import BASIC, DISAMBIGUATE


DOMAIN_BY_DEVICE_TYPE = {
    "网络设备": "网络",
    "路由器": "网络",
    "交换机": "网络",
    "AP": "网络",
    "AC": "网络",
    "服务器": "服务器",
    "服务器设备": "服务器",
    "存储设备": "存储",
    "FC交换机": "存储",
    "OLT": "PON",
    "ONU": "PON",
    "PON设备": "PON",
    "终端设备": "终端",
    "终端": "终端",
    "子网": "网络",
    "链路": "网络",
}

INFORMATION_RESULT_FORMS = {"列表", "数量", "基础信息", "属性信息", "概览"}


@dataclass
class RankedCapability:
    """包含能力卡、确定性匹配分数和可解释匹配原因的排序结果。"""

    card: CapabilityCard
    match_score: int
    match_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """将能力卡内容与匹配分数、匹配原因合并为 Prompt 输入字典。"""
        data = self.card.to_dict()
        data["match_score"] = self.match_score
        data["match_reasons"] = self.match_reasons
        return data


def load_capability_cards() -> List[CapabilityCard]:
    """从包内 JSON 配置加载能力卡。"""
    path = resources.files("question_recommendation").joinpath("data/capability_cards.json")
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    return [
        CapabilityCard.from_dict(item)
        for item in document
        if isinstance(item, Mapping)
    ]


def recommend_capabilities(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable] = (),
    cards: Sequence[CapabilityCard] = (),
    limit: int = 12,
) -> List[RankedCapability]:
    """确定性过滤、排序并选择候选能力卡，不调用 LLM 或 Embedding。"""
    available_cards = list(cards) if cards else load_capability_cards()
    ranked = []
    for card in available_cards:
        if _has_hard_conflict(context, card):
            continue
        score, reasons = _score_card(context, card, metadata_tables)
        ranked.append(RankedCapability(card=card, match_score=score, match_reasons=reasons))

    ranked.sort(key=lambda item: (-item.match_score, -item.card.priority, item.card.capability_id))
    return _select_diverse(ranked, context, limit)


def _has_hard_conflict(context: RecommendationContext, card: CapabilityCard) -> bool:
    """
    判断能力卡是否与标准上下文存在明确冲突。

    仅在意图、对象、已确认领域、能力策略或恢复策略明确不兼容时过滤；
    信息缺失和逻辑表相关度不参与硬过滤。
    """
    target_objects = context.subcomponent_types or context.device_types
    recovery_strategy = context.recovery_strategy
    ambiguous_domain = recovery_strategy == DISAMBIGUATE
    confirmed_domains = {
        DOMAIN_BY_DEVICE_TYPE[item]
        for item in context.device_types
        if item in DOMAIN_BY_DEVICE_TYPE
    }

    if confirmed_domains and len(confirmed_domains) == 1:
        if card.domain and card.domain not in confirmed_domains:
            return True

    if target_objects and not set(target_objects).intersection(card.objects):
        parent_recovery = (
            bool(recovery_strategy)
            and recovery_strategy in card.recovery_strategies
            and bool(set(context.device_types).intersection(card.objects))
        )
        if not parent_recovery:
            return True

    if recovery_strategy == BASIC and not _is_information_card(card):
        return True

    if context.intention and card.intent_type != context.intention:
        if not recovery_strategy or recovery_strategy not in card.recovery_strategies:
            return True

    if recovery_strategy and recovery_strategy not in card.recovery_strategies:
        return True

    if _policy_rejects(card.metric_policy, context.kpis):
        if not ((ambiguous_domain or recovery_strategy == BASIC) and _is_information_card(card)):
            return True
    if _policy_rejects(card.attribute_policy, context.properties):
        return True

    if context.aggregations and card.aggregations:
        if not set(context.aggregations).intersection(card.aggregations):
            return True

    return False


def _policy_rejects(policy: Mapping[str, Any], values: Sequence[str]) -> bool:
    """判断指标或属性值是否被能力策略的白名单或黑名单明确拒绝。"""
    if not values or not isinstance(policy, Mapping):
        return False
    mode = str(policy.get("mode", "") or "")
    allowed = set(str(item) for item in policy.get("allow", []) if item)
    denied = set(str(item) for item in policy.get("deny", []) if item)
    if denied.intersection(values):
        return True
    return mode == "allow" and bool(allowed) and not allowed.intersection(values)


def _score_card(
    context: RecommendationContext,
    card: CapabilityCard,
    metadata_tables: Sequence[MetadataTable],
) -> Tuple[int, List[str]]:
    """
    计算能力卡与上下文的确定性匹配分数。

    返回静态优先级叠加意图、对象、领域、指标、属性、聚合、定位和元数据相关度
    后的总分，以及对应的可解释匹配原因。
    """
    score = card.priority
    reasons: List[str] = []
    target_objects = context.subcomponent_types or context.device_types
    confirmed_domains = {
        DOMAIN_BY_DEVICE_TYPE[item]
        for item in context.device_types
        if item in DOMAIN_BY_DEVICE_TYPE
    }

    if len(confirmed_domains) == 1 and card.domain in confirmed_domains:
        score += 100
        reasons.append("业务域匹配")
    if context.intention and card.intent_type == context.intention:
        score += 80
        reasons.append("查询意图匹配")
    if context.recovery_strategy and context.recovery_strategy in card.recovery_strategies:
        score += 90
        reasons.append("恢复策略匹配")
    if set(target_objects).intersection(card.objects):
        score += 80
        reasons.append("查询对象匹配")
    if context.device_types and card.parent_object in context.device_types:
        score += 50
        reasons.append("父对象匹配")
    if _policy_matches(card.metric_policy, context.kpis):
        score += 35
        reasons.append("指标匹配")
    if _policy_matches(card.attribute_policy, context.properties):
        score += 25
        reasons.append("属性匹配")
    if context.aggregations and set(context.aggregations).intersection(card.aggregations):
        score += 20
        reasons.append("聚合算子匹配")

    locator_types = {item.id_type for item in context.identifiers}
    if locator_types.intersection(card.locators):
        score += 20
        reasons.append("定位方式匹配")

    flattened_metadata = " ".join(
        list(context.tables)
        + [
            text
            for table in metadata_tables
            for text in (
                table.table_name,
                table.table_description,
            )
            if text
        ]
        + [
            text
            for table in metadata_tables
            for column in table.columns
            for text in (
                column.column_name,
                column.column_description,
            )
            if text
        ]
    )
    if any(hint and hint in flattened_metadata for hint in card.table_hints):
        score += 20
        reasons.append("逻辑表或元数据相关")
    return score, reasons


def _policy_matches(policy: Mapping[str, Any], values: Sequence[str]) -> bool:
    """判断输入值是否被动态策略接受，或命中策略白名单。"""
    if not values or not isinstance(policy, Mapping):
        return False
    mode = str(policy.get("mode", "") or "")
    allowed = set(str(item) for item in policy.get("allow", []) if item)
    if mode in {"dynamic", "dynamic_inherit"}:
        return True
    return bool(allowed.intersection(values))


def _is_information_card(card: CapabilityCard) -> bool:
    """判断能力卡是否能作为列表、数量、基础信息等信息类恢复能力。"""
    return bool(INFORMATION_RESULT_FORMS.intersection(card.result_forms))


def _select_diverse(
    ranked: Sequence[RankedCapability],
    context: RecommendationContext,
    limit: int,
) -> List[RankedCapability]:
    """
    从已排序候选中选择稳定且具备多样性的 Top N。

    同一意图、对象和结果形态最多保留三张；领域歧义场景下，同一领域与父对象组合
    也最多保留三张，避免单一领域占满候选。
    """
    if limit <= 0:
        return []
    selected: List[RankedCapability] = []
    group_counts: Dict[Tuple[str, str, str], int] = {}
    domain_counts: Dict[Tuple[str, str], int] = {}
    ambiguous_domain = context.recovery_strategy == DISAMBIGUATE

    for item in ranked:
        card = item.card
        object_key = card.objects[0] if card.objects else ""
        form_key = card.result_forms[0] if card.result_forms else ""
        group_key = (card.intent_type, object_key, form_key)
        domain_key = (card.domain, card.parent_object)
        if group_counts.get(group_key, 0) >= 3:
            continue
        if ambiguous_domain and domain_counts.get(domain_key, 0) >= 3:
            continue
        selected.append(item)
        group_counts[group_key] = group_counts.get(group_key, 0) + 1
        domain_counts[domain_key] = domain_counts.get(domain_key, 0) + 1
        if len(selected) >= limit:
            break
    return selected
