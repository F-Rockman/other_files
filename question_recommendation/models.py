"""
问数推荐结构化输入模型。

模型保持轻量 dataclass 形态，方便在没有额外依赖的 Python 工具仓库中复用。
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _as_list(value: Any) -> List[str]:
    """将字符串、列表或空值规范为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        result = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


def _compact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """移除空值并合并 extra 字段。"""
    extra = data.pop("extra", None) or {}
    compacted = {
        key: value
        for key, value in data.items()
        if value not in (None, "", [], {})
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key not in compacted and value not in (None, "", [], {}):
                compacted[key] = value
    return compacted


def _known_fields(cls: type) -> set:
    return {item.name for item in fields(cls)}


@dataclass
class RecognizedIntent:
    """前一步意图识别结果。"""

    intent_type: str = ""
    subnet_info: Any = None
    device_info: Any = None
    sub_component_info: Any = None
    attribute_info: Any = None
    metric_info: Any = None
    time_info: Any = None
    alarm_info: Any = None
    aggregation_operator: Any = None
    domain_info: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "RecognizedIntent":
        if isinstance(data, cls):
            return data
        if not data:
            return cls()

        aliases = {
            "intent": "intent_type",
            "user_intent": "intent_type",
            "aggregation_info": "aggregation_operator",
            "agg_operator": "aggregation_operator",
            "business_domain": "domain_info",
            "domain": "domain_info",
        }
        known = _known_fields(cls)
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for key, value in dict(data).items():
            mapped_key = aliases.get(key, key)
            if mapped_key in known and mapped_key != "extra":
                kwargs[mapped_key] = value
            else:
                extra[key] = value
        kwargs["extra"] = extra
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass
class StructuredTemplate:
    """推荐模板的结构化能力单元。"""

    template_id: str = ""
    template_text: str = ""
    intent_tags: List[str] = field(default_factory=list)
    domain_tags: List[str] = field(default_factory=list)
    object_tags: List[str] = field(default_factory=list)
    parent_object: str = ""
    child_object: str = ""
    template_type: str = ""
    slots: List[str] = field(default_factory=list)
    supported_recovery_types: List[str] = field(default_factory=list)
    priority: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "StructuredTemplate":
        if isinstance(data, cls):
            return data
        if not data:
            return cls()

        aliases = {
            "id": "template_id",
            "text": "template_text",
            "template": "template_text",
            "recovery_types": "supported_recovery_types",
            "recovery_tags": "supported_recovery_types",
            "type": "template_type",
        }
        list_fields = {
            "intent_tags",
            "domain_tags",
            "object_tags",
            "slots",
            "supported_recovery_types",
        }
        known = _known_fields(cls)
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for key, value in dict(data).items():
            mapped_key = aliases.get(key, key)
            if mapped_key in known and mapped_key != "extra":
                if mapped_key in list_fields:
                    kwargs[mapped_key] = _as_list(value)
                elif mapped_key == "priority":
                    try:
                        kwargs[mapped_key] = int(value)
                    except (TypeError, ValueError):
                        kwargs[mapped_key] = 0
                else:
                    kwargs[mapped_key] = "" if value is None else str(value)
            else:
                extra[key] = value
        kwargs["extra"] = extra
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass
class MetadataColumn:
    """当前查询可见的原始表列元数据。"""

    table_name: str = ""
    column_name: str = ""
    data_type: str = ""
    comment: str = ""
    enum_meanings: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "MetadataColumn":
        if isinstance(data, cls):
            return data
        if not data:
            return cls()

        aliases = {
            "table": "table_name",
            "tableName": "table_name",
            "column": "column_name",
            "name": "column_name",
            "columnName": "column_name",
            "type": "data_type",
            "dataType": "data_type",
            "description": "comment",
            "enum": "enum_meanings",
            "enums": "enum_meanings",
        }
        known = _known_fields(cls)
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for key, value in dict(data).items():
            mapped_key = aliases.get(key, key)
            if mapped_key in known and mapped_key != "extra":
                kwargs[mapped_key] = value
            else:
                extra[key] = value
        kwargs["extra"] = extra
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return _compact_dict(asdict(self))
