"""内置领域卡和特殊卡的统一加载。"""

import json
from importlib import resources
from typing import Any, Dict, List, Tuple

from .models import DeviceCapabilityProfile, SpecialCapabilitySpec


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


def _load_capability_document() -> Dict[str, Any]:
    """读取六类骨架设备规格配置文档。"""
    path = resources.files("question_recommendation").joinpath(
        "data/device_capability_profiles.json"
    )
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    return document if isinstance(document, dict) else {}
