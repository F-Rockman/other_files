"""特殊能力原问题设备词分析。"""

from dataclasses import dataclass
from typing import List, Sequence

from .capability_matching import (
    domain_card_alias_supported,
    domain_card_device_terms,
    normalize_match_value,
    normalized_set,
    specific_terms_in_text,
)
from .models import DeviceCapabilityProfile


@dataclass
class SpecialDeviceTermAnalysis:
    """原问题中与特殊能力相关的设备词兼容性分析。"""

    compatible_device_terms: List[str]


def empty_special_device_term_analysis() -> SpecialDeviceTermAnalysis:
    """返回空的特殊设备词分析结果。"""
    return SpecialDeviceTermAnalysis([])


def special_device_term_supported(
    value: str,
    supported: Sequence[str],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> bool:
    """判断设备词是否能通过标准类型或别名归一到特殊能力支持范围。"""
    supported_set = normalized_set(supported)
    normalized_value = normalize_match_value(value)
    if not supported_set:
        return True
    if normalized_value in supported_set:
        return True
    return domain_card_alias_supported(value, supported_set, domain_cards)


def analyze_special_device_terms(
    text: str,
    supported_device_types: Sequence[str],
    domain_cards: Sequence[DeviceCapabilityProfile],
) -> SpecialDeviceTermAnalysis:
    """仅分析原问题中能力卡已知且与特殊能力兼容的设备词。"""
    if not supported_device_types:
        return empty_special_device_term_analysis()
    terms = specific_terms_in_text(text, domain_card_device_terms(domain_cards))
    compatible: List[str] = []
    for term in terms:
        if special_device_term_supported(term, supported_device_types, domain_cards):
            compatible.append(term)
    return SpecialDeviceTermAnalysis(compatible)
