"""能力卡对象、文本、定位方式和通用值匹配工具。"""

from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .capability_constants import (
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .models import (
    CapabilityCandidate,
    DeviceCondition,
    DeviceCapabilityProfile,
    RecommendationContext,
    SubcomponentCapabilitySpec,
)


def context_device_types(context: RecommendationContext) -> List[str]:
    """从设备条件实时派生去重后的原始设备类型，并保持首次出现顺序。"""
    result: List[str] = []
    seen = set()
    for condition in context.devices:
        device_type = str(condition.device_type or "").strip()
        normalized = normalize_match_value(device_type)
        if device_type and normalized not in seen:
            seen.add(normalized)
            result.append(device_type)
    return result


def device_conditions_for_types(device_types: Iterable[str]) -> List[DeviceCondition]:
    """将内部识别出的设备类型方向转换为不带定位值的设备条件。"""
    result: List[DeviceCondition] = []
    seen = set()
    for device_type in device_types:
        text = str(device_type or "").strip()
        normalized = normalize_match_value(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(DeviceCondition(device_type=text))
    return result


def locators_compatible(context: RecommendationContext, locators: Sequence[str]) -> bool:
    """忽略大小写判断仍有效的定位参数是否被设备规格支持。"""
    identifier_types = []
    for item in context.devices:
        if item.device_id:
            identifier_types.append(item.id_type)
    normalized_types = normalized_set(identifier_types)
    return not normalized_types or bool(
        normalized_types.intersection(normalized_set(locators))
    )


def is_subnet_context(context: RecommendationContext) -> bool:
    """判断上下文是否明确查询子网资源。"""
    return normalize_match_value("子网") in normalized_set(
        context_device_types(context) + context.subcomponent_types
    )


def matching_domain_cards(
    context: RecommendationContext,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按明确设备类型或子部件对象过滤设备规格。"""
    device_types = context_device_types(context)
    if device_types:
        return _domain_cards_matching_device_types(domain_cards, device_types)
    if context.subcomponent_types:
        return _domain_cards_matching_subcomponents(
            domain_cards, context.subcomponent_types
        )
    return list(domain_cards)


def _domain_cards_matching_device_types(
    domain_cards: Sequence[DeviceCapabilityProfile],
    device_types: Sequence[str],
) -> List[DeviceCapabilityProfile]:
    """返回命中任一明确设备类型的领域卡。"""
    matched = []
    for domain_card in domain_cards:
        if domain_card_matches_any(domain_card, device_types):
            matched.append(domain_card)
    return matched


def _domain_cards_matching_subcomponents(
    domain_cards: Sequence[DeviceCapabilityProfile],
    subcomponent_types: Sequence[str],
) -> List[DeviceCapabilityProfile]:
    """返回包含任一明确子部件的领域卡。"""
    matched = []
    for domain_card in domain_cards:
        if domain_card_has_matching_subcomponent(domain_card, subcomponent_types):
            matched.append(domain_card)
    return matched


def domain_card_device_terms(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型和别名。"""
    terms = []
    for domain_card in domain_cards:
        terms.extend(domain_card.device_types)
        terms.extend(domain_card.aliases)
    return terms


def domain_card_standard_device_types(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """按能力卡顺序展开所有标准设备类型。"""
    device_types = []
    for domain_card in domain_cards:
        device_types.extend(domain_card.device_types)
    return device_types


def domain_card_ids(domain_cards: Sequence[DeviceCapabilityProfile]) -> set:
    """返回能力卡标识集合。"""
    return {domain_card.profile_id for domain_card in domain_cards}


def domain_card_matches_any(
    domain_card: DeviceCapabilityProfile,
    device_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否命中任一设备类型。"""
    for device_type in device_types:
        if domain_card.matches(device_type):
            return True
    return False


def domain_card_has_matching_subcomponent(
    domain_card: DeviceCapabilityProfile,
    subcomponent_types: Sequence[str],
) -> bool:
    """判断设备能力卡是否包含任一匹配的子部件。"""
    for spec in domain_card.subcomponents:
        if subcomponent_matches_any(spec, subcomponent_types):
            return True
    return False


def subcomponent_matches_any(
    spec: SubcomponentCapabilitySpec,
    subcomponent_types: Sequence[str],
) -> bool:
    """判断子部件能力是否命中任一子部件类型。"""
    for subcomponent_type in subcomponent_types:
        if spec.matches(subcomponent_type):
            return True
    return False


def domain_card_alias_supported(
    value: str,
    supported: set,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断设备别名是否能够映射到特殊能力支持的标准设备类型。"""
    for domain_card in domain_cards:
        if not domain_card.matches(value):
            continue
        if supported.intersection(normalized_set(domain_card.device_types)):
            return True
    return False


def domain_cards_matching_question_direction(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按原问题中的业务域、设备类型或别名匹配领域卡。"""
    object_card_ids = domain_card_ids(domain_cards_matching_text(text, domain_cards))
    domains = [domain_card.domain for domain_card in domain_cards]
    matched_domains = normalized_set(specific_terms_in_text(text, domains))
    matched_domain_cards = []
    for domain_card in domain_cards:
        if domain_card.profile_id in object_card_ids:
            matched_domain_cards.append(domain_card)
            continue
        if normalize_match_value(domain_card.domain) in matched_domains:
            matched_domain_cards.append(domain_card)
    return matched_domain_cards


def domain_cards_matching_text(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按文本中未被更长对象词覆盖的设备类型或别名匹配领域卡。"""
    matched_terms = normalized_set(
        specific_terms_in_text(text, domain_card_device_terms(domain_cards))
    )
    matched_domain_cards = []
    for domain_card in domain_cards:
        card_terms = normalized_set(domain_card.device_types + domain_card.aliases)
        if matched_terms.intersection(card_terms):
            matched_domain_cards.append(domain_card)
    return matched_domain_cards


def subcomponents_matching_text(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """按原问题中的子部件类型或别名匹配父设备与子部件规格。"""
    matched_terms = normalized_set(
        specific_terms_in_text(text, _subcomponent_terms(domain_cards))
    )
    matched_subcomponents = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            spec_terms = normalized_set(spec.types + spec.aliases)
            if matched_terms.intersection(spec_terms):
                matched_subcomponents.append((domain_card, spec))
    return matched_subcomponents


def _subcomponent_terms(
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[str]:
    """展开全部领域卡中的子部件标准类型和别名。"""
    terms = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            terms.extend(spec.types)
            terms.extend(spec.aliases)
    return terms


def specific_terms_in_text(text: str, terms: Sequence[str]) -> List[str]:
    """返回明确对象词，并移除被更长对象词完整覆盖的短词。"""
    matches = _term_occurrences(normalize_match_value(text), terms)
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
        matches.extend(_single_term_occurrences(normalized_text, normalized_term, term))
    return matches


def _unique_normalized_terms(terms: Sequence[str]) -> Dict[str, str]:
    """按首次出现顺序返回规范词项到展示词项的映射。"""
    unique_terms = {}
    for term in terms:
        normalized_term = normalize_match_value(term)
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


def examples_for_type(
    examples: Sequence[str],
    capability_type: str,
    metrics: Sequence[str],
) -> List[str]:
    """只保留与当前六类骨架一致的表达示例。"""
    result = []
    for example in examples:
        if _example_matches_type(example, capability_type, metrics):
            result.append(example)
    return result


def _example_matches_type(
    example: str,
    capability_type: str,
    metrics: Sequence[str],
) -> bool:
    """判断自然问法示例是否与指定能力骨架一致。"""
    is_count = contains_any(example, ("数量", "总数"))
    is_metric = _is_metric_example(example, metrics)
    if capability_type in {DEVICE_COUNT, SUBCOMPONENT_COUNT}:
        return is_count
    if capability_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC}:
        return is_metric
    if capability_type in {DEVICE_INFO, SUBCOMPONENT_INFO}:
        return not is_count and not is_metric
    return False


def _is_metric_example(example: str, metrics: Sequence[str]) -> bool:
    """仅按当前能力卡指标名称判断示例是否属于指标查询。"""
    return contains_any(example, metrics)


def dedupe_candidates(
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


def dedupe_domain_cards(
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


def contains_capability_type(
    candidates: Sequence[CapabilityCandidate],
    capability_type: str,
) -> bool:
    """判断候选列表是否包含指定能力骨架。"""
    for candidate in candidates:
        if candidate.capability_type == capability_type:
            return True
    return False


def contains_any(text: str, values: Sequence[str]) -> bool:
    """忽略大小写判断文本是否包含任一非空能力卡字段值。"""
    normalized_text = normalize_match_value(text)
    for value in values:
        normalized_value = normalize_match_value(value)
        if normalized_value and normalized_value in normalized_text:
            return True
    return False


def normalize_match_value(value: Any) -> str:
    """规范能力卡匹配文本，忽略首尾空白与大小写。"""
    return str(value or "").strip().casefold()


def normalized_set(values: Iterable[Any]) -> set:
    """返回去除空值并忽略大小写的能力卡字段集合。"""
    result = set()
    for value in values:
        normalized = normalize_match_value(value)
        if normalized:
            result.add(normalized)
    return result


def has_overlap(left: Iterable[Any], right: Iterable[Any]) -> bool:
    """忽略大小写判断两组能力卡字段值是否存在交集。"""
    return bool(normalized_set(left).intersection(normalized_set(right)))


def values_equal(left: Any, right: Any) -> bool:
    """忽略大小写判断两个能力卡字段值是否相等。"""
    return normalize_match_value(left) == normalize_match_value(right)
