"""能力词 span 匹配与设备词强弱归属匹配工具。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .models import DeviceCapabilityProfile, SubcomponentCapabilitySpec


@dataclass(frozen=True)
class SubcomponentTextMatches:
    """原问题文本中的子部件基础命中和子部件指标命中。"""

    basic: List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]
    explicit_metric: List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]


@dataclass(frozen=True)
class _ScopedTermMatch:
    """带所属能力卡和文本范围的能力词命中。"""

    term: str
    start: int
    end: int
    domain_card: DeviceCapabilityProfile
    subcomponent: SubcomponentCapabilitySpec


def match_domain_cards_by_device_terms(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[DeviceCapabilityProfile]:
    """按 device_types 强命中优先于 aliases 弱命中的规则匹配领域卡。"""
    matched_terms = _specific_device_terms_in_text(text, _domain_card_device_terms(domain_cards))
    matched_keys = {_device_match_key(term) for term in matched_terms}
    strong_keys = _matched_strong_device_keys(matched_keys, domain_cards)
    matched_domain_cards = []
    for domain_card in domain_cards:
        if _card_matches_strong_device_key(domain_card, matched_keys):
            matched_domain_cards.append(domain_card)
            continue
        if _card_matches_alias_device_key(domain_card, matched_keys, strong_keys):
            matched_domain_cards.append(domain_card)
    return matched_domain_cards


def match_subcomponents_by_text(
    text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> SubcomponentTextMatches:
    """返回文本中可触发基础能力或指标能力的子部件命中。"""
    normalized_text = _device_match_key(text)
    subcomponent_matches = _subcomponent_term_matches(normalized_text, domain_cards)
    field_matches = _field_term_matches(normalized_text, domain_cards)
    metric_field_matches = _metric_field_matches(normalized_text, domain_cards)
    metric_matches = _subcomponent_metric_matches(normalized_text, domain_cards)
    explicit_metric_matches = _explicit_subcomponent_metric_matches(
        subcomponent_matches, metric_field_matches, metric_matches
    )
    return SubcomponentTextMatches(
        basic=_dedupe_match_pairs(
            _uncovered_subcomponent_matches(subcomponent_matches, field_matches)
        ),
        explicit_metric=_dedupe_match_pairs(explicit_metric_matches),
    )


def _subcomponent_term_matches(
    normalized_text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[_ScopedTermMatch]:
    """返回全部子部件类型和别名命中。"""
    matches: List[_ScopedTermMatch] = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            matches.extend(
                _scoped_term_occurrences(
                    normalized_text,
                    spec.types + spec.aliases,
                    domain_card,
                    spec,
                )
            )
    return _longest_scoped_matches(matches)


def _field_term_matches(
    normalized_text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[_ScopedTermMatch]:
    """返回全部属性和指标命中，用于覆盖短对象词。"""
    matches: List[_ScopedTermMatch] = []
    for domain_card in domain_cards:
        matches.extend(
            _scoped_term_occurrences(
                normalized_text, domain_card.properties + domain_card.metrics, domain_card
            )
        )
        for spec in domain_card.subcomponents:
            matches.extend(
                _scoped_term_occurrences(
                    normalized_text, spec.properties + spec.metrics, domain_card, spec
                )
            )
    return _longest_scoped_matches(matches)


def _metric_field_matches(
    normalized_text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[_ScopedTermMatch]:
    """返回全部设备和子部件指标命中。"""
    matches: List[_ScopedTermMatch] = []
    for domain_card in domain_cards:
        matches.extend(
            _scoped_term_occurrences(normalized_text, domain_card.metrics, domain_card)
        )
        for spec in domain_card.subcomponents:
            matches.extend(
                _scoped_term_occurrences(
                    normalized_text, spec.metrics, domain_card, spec
                )
            )
    return _longest_scoped_matches(matches)


def _subcomponent_metric_matches(
    normalized_text: str,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> List[_ScopedTermMatch]:
    """返回子部件指标命中。"""
    matches: List[_ScopedTermMatch] = []
    for domain_card in domain_cards:
        for spec in domain_card.subcomponents:
            matches.extend(
                _scoped_term_occurrences(
                    normalized_text, spec.metrics, domain_card, spec
                )
            )
    return _longest_scoped_matches(matches)


def _explicit_subcomponent_metric_matches(
    subcomponent_matches: Sequence[_ScopedTermMatch],
    metric_field_matches: Sequence[_ScopedTermMatch],
    metric_matches: Sequence[_ScopedTermMatch],
) -> List[_ScopedTermMatch]:
    """返回明确以子部件为对象的指标命中。"""
    result = _metric_matches_containing_subcomponent(metric_matches)
    for match in subcomponent_matches:
        if not _subcomponent_has_metric_in_text(match, metric_field_matches):
            continue
        result.append(match)
    return _longest_scoped_matches(result)


def _metric_matches_containing_subcomponent(
    metric_matches: Sequence[_ScopedTermMatch],
) -> List[_ScopedTermMatch]:
    """返回指标词自身包含子部件名称的命中。"""
    result = []
    for match in metric_matches:
        if _term_contains_any(match.term, match.subcomponent.types + match.subcomponent.aliases):
            result.append(match)
    return result


def _subcomponent_has_metric_in_text(
    match: _ScopedTermMatch,
    metric_field_matches: Sequence[_ScopedTermMatch],
) -> bool:
    """判断显式子部件后是否伴随该子部件支持的指标。"""
    for metric_match in metric_field_matches:
        if _metric_belongs_to_subcomponent(metric_match.term, match.subcomponent):
            return True
    return False


def _metric_belongs_to_subcomponent(
    metric_term: str,
    spec: SubcomponentCapabilitySpec,
) -> bool:
    """通过能力卡指标名判断文本指标是否属于指定子部件。"""
    normalized_metric = _device_match_key(metric_term)
    for spec_metric in spec.metrics:
        normalized_spec_metric = _device_match_key(spec_metric)
        if not normalized_metric or not normalized_spec_metric:
            continue
        if normalized_metric in normalized_spec_metric:
            return True
        if normalized_spec_metric in normalized_metric:
            return True
    return False


def _term_contains_any(term: str, values: Sequence[str]) -> bool:
    """判断词项是否包含任一非空能力词。"""
    normalized_term = _device_match_key(term)
    for value in values:
        normalized_value = _device_match_key(value)
        if normalized_value and normalized_value in normalized_term:
            return True
    return False


def _scoped_term_occurrences(
    normalized_text: str,
    terms: Sequence[str],
    domain_card: DeviceCapabilityProfile,
    spec: SubcomponentCapabilitySpec = SubcomponentCapabilitySpec(),
) -> List[_ScopedTermMatch]:
    """返回指定能力来源的一组词项命中。"""
    matches = []
    for term, start, end in _device_term_occurrences(normalized_text, terms):
        matches.append(_ScopedTermMatch(term, start, end, domain_card, spec))
    return matches


def _longest_scoped_matches(
    matches: Sequence[_ScopedTermMatch],
) -> List[_ScopedTermMatch]:
    """移除同类词项中被更长词完整覆盖的命中。"""
    result = []
    for match in matches:
        if _is_scoped_match_covered(match, matches):
            continue
        result.append(match)
    return result


def _is_scoped_match_covered(
    match: _ScopedTermMatch,
    matches: Sequence[_ScopedTermMatch],
) -> bool:
    """判断一个 scoped 命中是否被更长 scoped 命中覆盖。"""
    for other in matches:
        if other.start > match.start or other.end < match.end:
            continue
        if len(other.term) > len(match.term):
            return True
    return False


def _uncovered_subcomponent_matches(
    matches: Sequence[_ScopedTermMatch],
    field_matches: Sequence[_ScopedTermMatch],
) -> List[_ScopedTermMatch]:
    """过滤被更长属性或指标词覆盖的子部件基础命中。"""
    result = []
    for match in matches:
        if _is_covered_by_field_match(match, field_matches):
            continue
        result.append(match)
    return result


def _is_covered_by_field_match(
    match: _ScopedTermMatch,
    field_matches: Sequence[_ScopedTermMatch],
) -> bool:
    """判断子部件词范围是否被更长属性或指标词覆盖。"""
    for field_match in field_matches:
        if field_match.start > match.start or field_match.end < match.end:
            continue
        if field_match.end - field_match.start > match.end - match.start:
            return True
    return False


def _dedupe_match_pairs(
    matches: Sequence[_ScopedTermMatch],
) -> List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]]:
    """按能力卡和子部件规格去重并保留首次命中。"""
    result: List[Tuple[DeviceCapabilityProfile, SubcomponentCapabilitySpec]] = []
    seen = set()
    for match in matches:
        key = (match.domain_card.profile_id, tuple(match.subcomponent.types))
        if key in seen:
            continue
        seen.add(key)
        result.append((match.domain_card, match.subcomponent))
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


def _specific_device_terms_in_text(text: str, terms: Sequence[str]) -> List[str]:
    """返回设备词命中结果，仅忽略英文字母大小写和首尾空白。"""
    matches = _device_term_occurrences(_device_match_key(text), terms)
    result: List[str] = []
    for term, start, end in matches:
        if _is_covered_by_longer_term(term, start, end, matches):
            continue
        if term not in result:
            result.append(term)
    return result


def _device_term_occurrences(
    normalized_text: str,
    terms: Sequence[str],
) -> List[Tuple[str, int, int]]:
    """返回设备词在紧凑规范文本中的全部出现位置。"""
    matches: List[Tuple[str, int, int]] = []
    for normalized_term, term in _unique_device_terms(terms).items():
        matches.extend(_single_term_occurrences(normalized_text, normalized_term, term))
    return matches


def _unique_device_terms(terms: Sequence[str]) -> Dict[str, str]:
    """按首次出现顺序返回设备词紧凑规范值到展示词的映射。"""
    unique_terms = {}
    for term in terms:
        normalized_term = _device_match_key(term)
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
        end = start + len(normalized_term)
        if not _ascii_term_embedded(normalized_text, normalized_term, start, end):
            matches.append((term, start, end))
        start = normalized_text.find(normalized_term, start + 1)
    return matches


def _ascii_term_embedded(
    normalized_text: str,
    normalized_term: str,
    start: int,
    end: int,
) -> bool:
    """判断纯 ASCII 词是否嵌在更长英文数字词内部。"""
    if not (normalized_term.isascii() and normalized_term.isalnum()):
        return False
    before = start > 0 and normalized_text[start - 1].isascii()
    before = before and normalized_text[start - 1].isalnum()
    after = end < len(normalized_text) and normalized_text[end].isascii()
    after = after and normalized_text[end].isalnum()
    return before or after


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


def _matched_strong_device_keys(
    matched_keys: set,
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> set:
    """返回已由任一卡 device_types 强命中的词项。"""
    strong_keys = set()
    for domain_card in domain_cards:
        for device_type in domain_card.device_types:
            key = _device_match_key(device_type)
            if key in matched_keys:
                strong_keys.add(key)
    return strong_keys


def _card_matches_strong_device_key(
    domain_card: DeviceCapabilityProfile,
    matched_keys: set,
) -> bool:
    """判断领域卡的标准设备类型是否命中原问题。"""
    for device_type in domain_card.device_types:
        if _device_match_key(device_type) in matched_keys:
            return True
    return False


def _card_matches_alias_device_key(
    domain_card: DeviceCapabilityProfile,
    matched_keys: set,
    strong_keys: set,
) -> bool:
    """判断领域卡别名是否命中，且未被其他卡标准类型接管。"""
    for alias in domain_card.aliases:
        key = _device_match_key(alias)
        if key in matched_keys and key not in strong_keys:
            return True
    return False


def _device_match_key(value: Any) -> str:
    """生成设备词匹配 key，不做空格、连接符或写法归一。"""
    return str(value or "").strip().casefold()
