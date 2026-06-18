"""设备词强弱归属匹配工具。"""

from typing import Any, Dict, List, Sequence, Tuple

from .models import DeviceCapabilityProfile


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
