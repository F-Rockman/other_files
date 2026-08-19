"""内置领域卡和特殊卡的统一加载。"""

import json
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from .logical_model_reader import (
    business_names_from_document,
    dedupe_texts,
    load_logical_model_document,
)
from .models import (
    DeviceCapabilityProfile,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)


def load_capability_cards(
    logical_model_dir: Optional[str] = None,
) -> Tuple[
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
    if logical_model_dir:
        _expand_capability_card_sources(domain_cards, special_cards, logical_model_dir)
    return domain_cards, special_cards


def _load_capability_document() -> Dict[str, Any]:
    """读取六类骨架设备规格配置文档。"""
    path = resources.files("question_recommendation").joinpath(
        "data/device_capability_profiles.json"
    )
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    return document if isinstance(document, dict) else {}


def _expand_capability_card_sources(
    domain_cards: List[DeviceCapabilityProfile],
    special_cards: List[SpecialCapabilitySpec],
    logical_model_dir: str,
) -> None:
    """按来源逻辑表扩展能力卡属性和指标，并清空来源字段避免传给 LLM。"""
    field_cache: Dict[str, List[str]] = {}
    for domain_card in domain_cards:
        _expand_device_card_sources(domain_card, logical_model_dir, field_cache)
    for special_card in special_cards:
        special_card.properties = _merge_fields(
            special_card.properties,
            _source_business_names(
                special_card.property_sources,
                logical_model_dir,
                field_cache,
            ),
        )
        special_card.property_sources = []


def _expand_device_card_sources(
    domain_card: DeviceCapabilityProfile,
    logical_model_dir: str,
    field_cache: Dict[str, List[str]],
) -> None:
    """扩展一张设备卡及其子部件卡的属性和指标。"""
    domain_card.properties = _merge_fields(
        domain_card.properties,
        _source_business_names(
            domain_card.property_sources,
            logical_model_dir,
            field_cache,
        ),
    )
    domain_card.metrics = _merge_fields(
        domain_card.metrics,
        _source_business_names(
            domain_card.metric_sources,
            logical_model_dir,
            field_cache,
        ),
    )
    domain_card.property_sources = []
    domain_card.metric_sources = []
    for subcomponent in domain_card.subcomponents:
        _expand_subcomponent_sources(subcomponent, logical_model_dir, field_cache)


def _expand_subcomponent_sources(
    subcomponent: SubcomponentCapabilitySpec,
    logical_model_dir: str,
    field_cache: Dict[str, List[str]],
) -> None:
    """扩展一张子部件能力卡的属性和指标。"""
    subcomponent.properties = _merge_fields(
        subcomponent.properties,
        _source_business_names(
            subcomponent.property_sources,
            logical_model_dir,
            field_cache,
        ),
    )
    subcomponent.metrics = _merge_fields(
        subcomponent.metrics,
        _source_business_names(
            subcomponent.metric_sources,
            logical_model_dir,
            field_cache,
        ),
    )
    subcomponent.property_sources = []
    subcomponent.metric_sources = []


def _source_business_names(
    sources: List[str],
    logical_model_dir: str,
    field_cache: Dict[str, List[str]],
) -> List[str]:
    """按来源表名读取字段业务名，并缓存同一来源结果。"""
    result: List[str] = []
    for source in sources:
        if source not in field_cache:
            field_cache[source] = _load_source_business_names(source, logical_model_dir)
        result.extend(field_cache[source])
    return dedupe_texts(result)


def _load_source_business_names(
    source: str,
    logical_model_dir: str,
) -> List[str]:
    """读取单个逻辑模型文件中的 businessName_cn。"""
    document = load_logical_model_document(logical_model_dir, source)
    return business_names_from_document(document)


def _merge_fields(base_fields: List[str], loaded_fields: List[str]) -> List[str]:
    """将手写字段放在前面，再追加来源字段并去重。"""
    return dedupe_texts(base_fields + loaded_fields)
