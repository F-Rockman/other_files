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
    """
    前一步意图识别结果。

    Attributes:
        intent_type:
            用户核心查询意图。推荐值为 ``查信息``、``查告警``、``查指标``、
            ``查链路``。这是模板排序和 Prompt 判断的最高优先级字段。
        subnet_info:
            子网相关信息。可传字符串，也可传包含名称、匹配方式、范围等信息的字典。
            未涉及子网时留空。
        device_info:
            设备相关信息。建议传字典并保留设备类型、名称/IP 条件、匹配方式、
            是否匹配到多设备等信息。它用于保持查询对象和定位条件。
        sub_component_info:
            设备子部件信息，例如接口、端口、光模块、风扇。涉及子部件时建议填写，
            否则推荐可能退化为设备级问题。
        attribute_info:
            查询属性信息，例如健康状态、型号、厂商。可传字符串、列表或字典。
        metric_info:
            性能指标信息，例如 CPU 利用率、内存利用率、接收光功率。
            查指标场景建议填写。
        time_info:
            时间范围、粒度或时间匹配信息，例如最近24小时、按天。
        alarm_info:
            告警名称、级别、状态等告警相关信息。查告警场景建议填写。
        aggregation_operator:
            当前查询的聚合算子，例如平均值、最大值、数量、TopN。
        domain_info:
            业务域信息，例如网络、服务器、存储、PON。对于接口、光模块、硬盘等
            多业务域对象建议填写。
        tables:
            前一步意图识别关联到的逻辑表名列表。推荐器会为每个表读取
            ``{table_name}.logical.yaml``，自动构造表列元数据。
        extra:
            暂未标准化但需要透传给 LLM 的扩展信息。未知字典字段会自动进入 extra。
    """

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
    tables: List[str] = field(default_factory=list)
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
            "table_names": "tables",
        }
        known = _known_fields(cls)
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        for key, value in dict(data).items():
            mapped_key = aliases.get(key, key)
            if mapped_key in known and mapped_key != "extra":
                kwargs[mapped_key] = _as_list(value) if mapped_key == "tables" else value
            else:
                extra[key] = value
        kwargs["extra"] = extra
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass
class StructuredTemplate:
    """
    推荐模板的结构化能力单元。

    模板标签决定该模板是否可以进入推荐；``template_text`` 主要提供表达骨架。

    Attributes:
        template_id:
            模板稳定唯一标识，用于维护、排查和离线评估。强烈建议必填。
        template_text:
            可由 LLM 自然化改写的表达骨架，例如
            ``查询{定位方式}的网络设备接口列表``。必填。
        intent_tags:
            模板支持的用户意图，例如 ``["查信息"]``、``["查指标"]``。建议必填。
            它只提供排序信号，不能单独证明业务域或对象匹配。
        domain_tags:
            模板适用业务域，例如 ``["网络"]``。多业务域对象必须填写；不填时无法
            依靠该字段阻止跨域推荐。
        object_tags:
            模板涉及的对象列表，通常按父对象到子对象排列，例如
            ``["网络设备", "接口"]``。建议必填，是防止对象跑偏的主要字段。
        parent_object:
            父对象，例如 ``网络设备``。涉及设备与子部件关系时建议填写。
        child_object:
            子对象，例如 ``接口``。涉及子部件时建议填写。
        template_type:
            模板查询形态，例如列表、数量、基础信息、指标、趋势、告警列表、链路。
            用于失败兜底和排序，建议必填。
        slots:
            模板需要填充或继承的槽位，例如 ``["device_ip_or_name"]``。
            没有槽位时可以为空。
        supported_recovery_types:
            模板适合处理的失败类型，例如对象定位失败、父对象定位失败、条件过细、
            匹配到多设备。用于 error 场景排序。
        priority:
            外部召回分之外的静态优先级，值越大越优先。默认 0。
        extra:
            未标准化模板标签的透传容器。未知字典字段会自动进入 extra。
    """

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
    """
    当前查询可见的表列业务描述。

    调用侧按列传入，推荐器在构造 Prompt 时会按 ``table_name`` 和
    ``table_description`` 聚合为多表结构。元数据是辅助信息，不是推荐能力边界；
    没有元数据时，推荐仍可依靠结构化意图和模板工作。

    Attributes:
        table_name:
            字段所属表名或对象表标识。
        table_description:
            表的自然语言业务描述，例如网络设备、网络设备性能指标。
        column_name:
            物理列名或字段标识。
        column_description:
            列的自然语言业务描述，例如 CPU 利用率、健康状态。
        其它输入字段会被忽略，不会传给 LLM。
    """

    table_name: str = ""
    table_description: str = ""
    column_name: str = ""
    column_description: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "MetadataColumn":
        if isinstance(data, cls):
            return data
        if not data:
            return cls()

        aliases = {
            "table": "table_name",
            "tableName": "table_name",
            "table_desc": "table_description",
            "tableDescription": "table_description",
            "column": "column_name",
            "name": "column_name",
            "columnName": "column_name",
            "column_desc": "column_description",
            "columnDescription": "column_description",
            "comment": "column_description",
            "description": "column_description",
        }
        known = _known_fields(cls)
        kwargs: Dict[str, Any] = {}
        for key, value in dict(data).items():
            mapped_key = aliases.get(key, key)
            if mapped_key in known:
                kwargs[mapped_key] = value
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return _compact_dict(asdict(self))
