"""内置领域卡和特殊卡的统一加载。"""

import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .models import (
    DeviceCapabilityProfile,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)


def load_capability_cards(
    logical_model_path_provider: Optional[str] = None,
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
    if logical_model_path_provider:
        _expand_capability_card_sources(
            domain_cards, special_cards, logical_model_path_provider
        )
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
    logical_model_path_provider: str,
) -> None:
    """按来源逻辑表扩展能力卡属性和指标，并清空来源字段避免传给 LLM。"""
    field_cache: Dict[str, List[str]] = {}
    for domain_card in domain_cards:
        _expand_device_card_sources(domain_card, logical_model_path_provider, field_cache)
    for special_card in special_cards:
        special_card.properties = _merge_fields(
            special_card.properties,
            _source_business_names(
                special_card.property_sources,
                logical_model_path_provider,
                field_cache,
            ),
        )
        special_card.property_sources = []


def _expand_device_card_sources(
    domain_card: DeviceCapabilityProfile,
    logical_model_path_provider: str,
    field_cache: Dict[str, List[str]],
) -> None:
    """扩展一张设备卡及其子部件卡的属性和指标。"""
    domain_card.properties = _merge_fields(
        domain_card.properties,
        _source_business_names(
            domain_card.property_sources,
            logical_model_path_provider,
            field_cache,
        ),
    )
    domain_card.metrics = _merge_fields(
        domain_card.metrics,
        _source_business_names(
            domain_card.metric_sources,
            logical_model_path_provider,
            field_cache,
        ),
    )
    domain_card.property_sources = []
    domain_card.metric_sources = []
    for subcomponent in domain_card.subcomponents:
        _expand_subcomponent_sources(
            subcomponent, logical_model_path_provider, field_cache
        )


def _expand_subcomponent_sources(
    subcomponent: SubcomponentCapabilitySpec,
    logical_model_path_provider: str,
    field_cache: Dict[str, List[str]],
) -> None:
    """扩展一张子部件能力卡的属性和指标。"""
    subcomponent.properties = _merge_fields(
        subcomponent.properties,
        _source_business_names(
            subcomponent.property_sources,
            logical_model_path_provider,
            field_cache,
        ),
    )
    subcomponent.metrics = _merge_fields(
        subcomponent.metrics,
        _source_business_names(
            subcomponent.metric_sources,
            logical_model_path_provider,
            field_cache,
        ),
    )
    subcomponent.property_sources = []
    subcomponent.metric_sources = []


def _source_business_names(
    sources: List[str],
    logical_model_path_provider: str,
    field_cache: Dict[str, List[str]],
) -> List[str]:
    """按来源表名读取字段业务名，并缓存同一来源结果。"""
    result: List[str] = []
    for source in sources:
        if source not in field_cache:
            field_cache[source] = _load_source_business_names(
                source, logical_model_path_provider
            )
        result.extend(field_cache[source])
    return _dedupe(result)


def _load_source_business_names(
    source: str,
    logical_model_path_provider: str,
) -> List[str]:
    """读取单个逻辑模型文件中的 businessName_cn。"""
    base_dir = _logical_model_base_dir(logical_model_path_provider)
    if base_dir is None:
        return []
    file_path = _logical_file_path(base_dir, source)
    if file_path is None or not file_path.is_file():
        return []
    try:
        import yaml

        with file_path.open("r", encoding="utf-8") as file:
            document = yaml.safe_load(file)
    except Exception:
        return []
    return _extract_business_names(document)


def _logical_model_base_dir(logical_model_path_provider: str) -> Optional[Path]:
    """将目录字符串转换为逻辑模型目录；无效路径按无来源处理。"""
    if not logical_model_path_provider:
        return None
    base_dir = Path(logical_model_path_provider).expanduser().resolve()
    return base_dir if base_dir.is_dir() else None


def _logical_file_path(base_dir: Path, source: str) -> Optional[Path]:
    """构造安全的逻辑模型文件路径，非法来源名直接跳过。"""
    if not source or Path(source).name != source:
        return None
    candidate = (base_dir / f"{source}.logical.yaml").resolve()
    if candidate.parent != base_dir:
        return None
    return candidate


def _extract_business_names(document: Any) -> List[str]:
    """从逻辑模型文档字段中提取非空 businessName_cn。"""
    if not isinstance(document, Mapping):
        return []
    schema = document.get("schema")
    if not isinstance(schema, Mapping):
        return []
    fields = schema.get("fields")
    if not isinstance(fields, list):
        return []
    result = []
    for field in fields:
        if isinstance(field, Mapping):
            result.append(_text(field.get("businessName_cn")))
    return _dedupe(result)


def _merge_fields(base_fields: List[str], loaded_fields: List[str]) -> List[str]:
    """将手写字段放在前面，再追加来源字段并去重。"""
    return _dedupe(base_fields + loaded_fields)


def _dedupe(values: List[str]) -> List[str]:
    """按顺序去重并忽略空白字段。"""
    result = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _text(value: Any) -> str:
    """将任意值转换为去除首尾空白的文本。"""
    return "" if value is None else str(value).strip()
