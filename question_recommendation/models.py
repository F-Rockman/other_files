"""
问数推荐内部数据模型。

RecommendationContext 是推荐模块唯一消费的意图上下文。它不是上一步意图识别
结构的镜像，只保留能力召回、排序、元数据加载和 LLM 表达实际使用的字段。
"""

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _as_list(value: Any) -> List[str]:
    """将字符串、可迭代对象或空值规范为去重后的字符串列表。"""
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, Iterable) or isinstance(values, (dict, bytes)):
        values = [values]

    result: List[str] = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _compact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """移除字典中的空值，生成适合序列化和传入 Prompt 的紧凑结构。"""
    return {
        key: value
        for key, value in data.items()
        if value not in (None, "", [], {})
    }


def _known_fields(cls: type) -> set:
    """返回 dataclass 声明的字段名集合，用于忽略未定义的外部输入字段。"""
    return {item.name for item in fields(cls)}


@dataclass
class Identifier:
    """
    可继续继承到推荐问题中的对象定位条件。

    Attributes:
        value: IP、MAC、名称或其他定位值。
        id_type: 定位值类型，推荐使用 IP、MAC、NAME、OTHER。
        match_mode: 匹配模式，推荐使用 EXACT、PREFIX、SUFFIX、FUZZY。
    """

    value: str = ""
    id_type: str = ""
    match_mode: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "Identifier":
        """从兼容字典构造定位条件，并统一标识类型与匹配模式的大小写。"""
        if isinstance(data, cls):
            return data
        data = data or {}
        return cls(
            value=str(data.get("value", data.get("device_id", "")) or "").strip(),
            id_type=str(data.get("id_type", "") or "").strip().upper(),
            match_mode=str(data.get("match_mode", "") or "").strip().upper(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """将定位条件转换为不包含空字段的字典。"""
        return _compact_dict(asdict(self))


@dataclass
class AlarmCondition:
    """
    告警查询条件。

    Attributes:
        alarm_type: NAME、LEVEL 或 STATUS。
        alarm_value: 告警名称、级别或状态值。
    """

    alarm_type: str = ""
    alarm_value: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional["AlarmCondition"]:
        """从兼容字典构造告警条件；无有效类型和值时返回 ``None``。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return None
        result = cls(
            alarm_type=str(data.get("alarm_type", "") or "").strip().upper(),
            alarm_value=str(data.get("alarm_value", "") or "").strip(),
        )
        return result if result.alarm_type or result.alarm_value else None

    def to_dict(self) -> Dict[str, Any]:
        """将告警条件转换为不包含空字段的字典。"""
        return _compact_dict(asdict(self))


@dataclass
class RecommendationContext:
    """
    推荐模块使用的最小化标准上下文。

    Attributes:
        intention: 查信息、查告警、查指标或查链路，用于能力过滤和排序。
        question: 用户原始问题，仅用于保持推荐方向和自然表达。
        device_types: 明确设备类型，用于匹配设备对象和限定子部件父对象。
        subcomponent_types: 接口、光模块等子部件类型，存在时作为主要查询对象。
        identifiers: 仍然有效、允许继承的对象定位条件。
        properties: 用户查询的属性名称。
        kpis: 用户查询的性能指标名称。
        time: 时间条件的原始表达。
        alarm: 告警查询条件。
        aggregations: 规范化后的聚合算子。
        tables: 用于加载逻辑元数据并辅助能力排序的逻辑表名。
        recovery_strategy: 根据稳定错误码确定的推荐恢复策略；为空表示普通推荐。
        refusal_message: 共享 ErrorInfo 提供的标准用户说明。
        refusal_detail: LLM 拒答提供的本次详细原因，仅辅助最终表达。
        invalid_values: 已明确失败、禁止继续继承的条件值。
    """

    intention: str = ""
    question: str = ""
    device_types: List[str] = field(default_factory=list)
    subcomponent_types: List[str] = field(default_factory=list)
    identifiers: List[Identifier] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    kpis: List[str] = field(default_factory=list)
    time: str = ""
    alarm: Optional[AlarmCondition] = None
    aggregations: List[str] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    recovery_strategy: str = ""
    refusal_message: str = ""
    refusal_detail: str = ""
    invalid_values: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "RecommendationContext":
        """
        从标准上下文字典构造对象。

        未声明字段会被忽略；列表、定位条件、告警条件和文本字段会被规范化。
        """
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()

        known = _known_fields(cls)
        kwargs = {key: value for key, value in data.items() if key in known}
        kwargs["device_types"] = _as_list(kwargs.get("device_types"))
        kwargs["subcomponent_types"] = _as_list(kwargs.get("subcomponent_types"))
        kwargs["properties"] = _as_list(kwargs.get("properties"))
        kwargs["kpis"] = _as_list(kwargs.get("kpis"))
        kwargs["aggregations"] = _as_list(kwargs.get("aggregations"))
        kwargs["tables"] = _as_list(kwargs.get("tables"))
        kwargs["invalid_values"] = _as_list(kwargs.get("invalid_values"))
        kwargs["identifiers"] = [
            Identifier.from_dict(item)
            for item in kwargs.get("identifiers", [])
            if isinstance(item, (Identifier, Mapping))
        ]
        kwargs["alarm"] = AlarmCondition.from_dict(kwargs.get("alarm"))
        for key in (
            "intention",
            "question",
            "time",
            "recovery_strategy",
            "refusal_message",
            "refusal_detail",
        ):
            kwargs[key] = str(kwargs.get(key, "") or "").strip()
        return cls(**kwargs)

    @classmethod
    def from_json(cls, text: str) -> "RecommendationContext":
        """解析 JSON 文本并构造标准推荐上下文。"""
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> Dict[str, Any]:
        """将推荐上下文转换为不包含空字段的字典。"""
        data = asdict(self)
        return _compact_dict(data)

    def to_json(self) -> str:
        """将推荐上下文序列化为保留中文字符的 JSON 文本。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class CapabilityCard:
    """
    系统可推荐能力的结构化边界。

    ``golden_questions`` 仅指导 LLM 表达；领域、对象和策略字段决定能力是否可用。
    """

    capability_id: str = ""
    domain: str = ""
    intent_type: str = ""
    objects: List[str] = field(default_factory=list)
    parent_object: str = ""
    locators: List[str] = field(default_factory=list)
    attribute_policy: Dict[str, Any] = field(default_factory=dict)
    metric_policy: Dict[str, Any] = field(default_factory=dict)
    aggregations: List[str] = field(default_factory=list)
    result_forms: List[str] = field(default_factory=list)
    time_policy: str = ""
    recovery_strategies: List[str] = field(default_factory=list)
    table_hints: List[str] = field(default_factory=list)
    golden_questions: List[str] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "CapabilityCard":
        """
        从能力卡配置字典构造对象。

        数组字段会统一为去重字符串列表，策略字段仅接受字典，优先级会转换为整数。
        """
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()

        list_fields = {
            "objects",
            "locators",
            "aggregations",
            "result_forms",
            "recovery_strategies",
            "table_hints",
            "golden_questions",
        }
        kwargs: Dict[str, Any] = {}
        for key in _known_fields(cls):
            if key not in data:
                continue
            value = data[key]
            if key in list_fields:
                kwargs[key] = _as_list(value)
            elif key in {"attribute_policy", "metric_policy"}:
                kwargs[key] = dict(value) if isinstance(value, Mapping) else {}
            elif key == "priority":
                try:
                    kwargs[key] = int(value)
                except (TypeError, ValueError):
                    kwargs[key] = 0
            else:
                kwargs[key] = str(value or "").strip()
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """将能力卡转换为不包含空字段的字典，供排序结果和 Prompt 使用。"""
        return _compact_dict(asdict(self))


@dataclass
class MetadataColumn:
    """逻辑模型中一个字段的业务描述。"""

    column_name: str = ""
    column_description: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "MetadataColumn":
        """从字段元数据字典构造对象，并兼容常用列名与描述字段别名。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()

        aliases = {
            "column": "column_name",
            "name": "column_name",
            "columnName": "column_name",
            "column_desc": "column_description",
            "columnDescription": "column_description",
            "comment": "column_description",
            "description": "column_description",
        }
        kwargs: Dict[str, Any] = {}
        for key, value in data.items():
            mapped_key = aliases.get(key, key)
            if mapped_key in _known_fields(cls):
                kwargs[mapped_key] = str(value or "").strip()
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """将字段元数据转换为不包含空字段的字典。"""
        return _compact_dict(asdict(self))


@dataclass
class MetadataTable:
    """
    按逻辑表组织的业务元数据。

    Attributes:
        table_name: 逻辑模型根节点的 ``name``。
        table_description: 逻辑模型根节点的 ``description_cn``。
        columns: 当前表 ``schema.fields`` 中有效字段的业务描述。
    """

    table_name: str = ""
    table_description: str = ""
    columns: List[MetadataColumn] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "MetadataTable":
        """从按表组织的元数据字典构造对象，并规范化其中的字段列表。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()

        raw_columns = data.get("columns")
        if not isinstance(raw_columns, list):
            raw_columns = []
        columns = [
            MetadataColumn.from_dict(item)
            for item in raw_columns
            if isinstance(item, (MetadataColumn, Mapping))
        ]
        return cls(
            table_name=str(data.get("table_name", data.get("name", "")) or "").strip(),
            table_description=str(
                data.get("table_description", data.get("description_cn", "")) or ""
            ).strip(),
            columns=columns,
        )

    def to_dict(self) -> Dict[str, Any]:
        """将表及其字段元数据转换为适合 Prompt 的嵌套字典。"""
        return _compact_dict(
            {
                "table_name": self.table_name,
                "table_description": self.table_description,
                "columns": [column.to_dict() for column in self.columns],
            }
        )
