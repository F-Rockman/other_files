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


def _casefold_text(value: Any) -> str:
    """规范文本大小写，仅用于能力卡字段匹配，不改变原始展示值。"""
    return str(value or "").strip().casefold()


@dataclass
class DeviceCondition:
    """
    上游识别出的单个设备条件。

    四个字段保持与上游设备结构同构，使定位值、定位方式和设备类型始终保持
    对应关系。定位值失效时可以清空定位信息，同时继续保留已识别的设备类型。

    Attributes:
        device_id: 设备名称、IP、MAC 或其他具体定位值。
        id_type: 定位值类型，推荐使用 IP、MAC、NAME、OTHER。
        match_mode: 匹配模式，推荐使用 EXACT、PREFIX、SUFFIX、FUZZY。
        device_type: 该定位条件对应的原始设备类型。
    """

    device_id: str = ""
    id_type: str = ""
    match_mode: str = ""
    device_type: str = ""

    def __post_init__(self) -> None:
        """统一清理设备条件文本，并规范定位类型与匹配模式大小写。"""
        self.device_id = str(self.device_id or "").strip()
        self.id_type = str(self.id_type or "").strip().upper()
        self.match_mode = str(self.match_mode or "").strip().upper()
        self.device_type = str(self.device_type or "").strip()

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "DeviceCondition":
        """从新设备结构构造条件，并统一标识类型与匹配模式的大小写。"""
        if isinstance(data, cls):
            return data
        data = data if isinstance(data, Mapping) else {}
        return cls(
            device_id=str(data.get("device_id", "") or "").strip(),
            id_type=str(data.get("id_type", "") or "").strip().upper(),
            match_mode=str(data.get("match_mode", "") or "").strip().upper(),
            device_type=str(data.get("device_type", "") or "").strip(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """按上游字段名将设备条件转换为不包含空字段的字典。"""
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
class SubnetScope:
    """
    推荐问题可继承的子网范围。

    Attributes:
        path: 子网层级路径或上级范围。
        name: 当前子网名称。
    """

    path: str = ""
    name: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional["SubnetScope"]:
        """从上游子网对象构造范围；没有有效路径和名称时返回 ``None``。"""
        if isinstance(data, cls):
            data = asdict(data)
        if not isinstance(data, Mapping):
            return None
        result = cls(
            path=str(data.get("path", "") or "").strip(),
            name=str(data.get("name", "") or "").strip(),
        )
        return result if result.path or result.name else None

    def to_dict(self) -> Dict[str, Any]:
        """将子网范围转换为不包含空字段的字典。"""
        return _compact_dict(asdict(self))


@dataclass
class RecommendationContext:
    """
    推荐模块使用的最小化标准上下文。

    Attributes:
        intention: 查信息、查告警、查指标或查链路，用于能力过滤和排序。
        question: 用户原始问题，仅用于保持推荐方向和自然表达。
        devices: 保留定位值、定位方式与设备类型对应关系的设备条件。
        subcomponent_types: 接口、光模块等子部件类型，存在时作为主要查询对象。
        subnet: 仍然有效、允许继承的子网范围，不改变主要查询对象。
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
    devices: List[DeviceCondition] = field(default_factory=list)
    subcomponent_types: List[str] = field(default_factory=list)
    subnet: Optional[SubnetScope] = None
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
        kwargs["subcomponent_types"] = _as_list(kwargs.get("subcomponent_types"))
        kwargs["properties"] = _as_list(kwargs.get("properties"))
        kwargs["kpis"] = _as_list(kwargs.get("kpis"))
        kwargs["aggregations"] = _as_list(kwargs.get("aggregations"))
        kwargs["tables"] = _as_list(kwargs.get("tables"))
        kwargs["invalid_values"] = _as_list(kwargs.get("invalid_values"))
        raw_devices = kwargs.get("devices")
        parsed_devices: List[DeviceCondition] = []
        if isinstance(raw_devices, (list, tuple)):
            for item in raw_devices:
                if not isinstance(item, (DeviceCondition, Mapping)):
                    continue
                condition = DeviceCondition.from_dict(item)
                if condition.device_id or condition.device_type:
                    parsed_devices.append(condition)
        kwargs["devices"] = parsed_devices
        kwargs["subnet"] = SubnetScope.from_dict(kwargs.get("subnet"))
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
        data["devices"] = []
        for item in self.devices:
            compact_device = item.to_dict()
            if compact_device:
                data["devices"].append(compact_device)
        data["subnet"] = self.subnet.to_dict() if self.subnet else None
        data["alarm"] = self.alarm.to_dict() if self.alarm else None
        return _compact_dict(data)

    def to_json(self) -> str:
        """将推荐上下文序列化为保留中文字符的 JSON 文本。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class SubcomponentCapabilitySpec:
    """设备下一个同能力子部件族的能力规格。"""

    types: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    table_hints: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(
        cls, data: Optional[Mapping[str, Any]]
    ) -> "SubcomponentCapabilitySpec":
        """从配置字典构造子部件能力规格。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            types=_as_list(data.get("types")),
            aliases=_as_list(data.get("aliases")),
            properties=_as_list(data.get("properties")),
            metrics=_as_list(data.get("metrics")),
            table_hints=_as_list(data.get("table_hints")),
            examples=_as_list(data.get("examples")),
            priority=_as_int(data.get("priority")),
        )

    def matches(self, value: str) -> bool:
        """忽略大小写判断输入对象是否命中子部件标准类型或别名。"""
        normalized = _casefold_text(value)
        return bool(normalized) and normalized in {
            _casefold_text(item) for item in self.types + self.aliases
        }

    def to_dict(self) -> Dict[str, Any]:
        """将子部件规格转换为紧凑字典。"""
        return _compact_dict(asdict(self))


@dataclass
class DeviceCapabilityProfile:
    """一种设备及其嵌套子部件的完整推荐能力规格。"""

    profile_id: str = ""
    domain: str = ""
    device_types: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    locators: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    subcomponents: List[SubcomponentCapabilitySpec] = field(default_factory=list)
    table_hints: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "DeviceCapabilityProfile":
        """从配置字典构造设备能力规格。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            profile_id=str(data.get("profile_id", "") or "").strip(),
            domain=str(data.get("domain", "") or "").strip(),
            device_types=_as_list(data.get("device_types")),
            aliases=_as_list(data.get("aliases")),
            locators=_as_list(data.get("locators")),
            properties=_as_list(data.get("properties")),
            metrics=_as_list(data.get("metrics")),
            subcomponents=[
                SubcomponentCapabilitySpec.from_dict(item)
                for item in data.get("subcomponents", [])
                if isinstance(item, (SubcomponentCapabilitySpec, Mapping))
            ],
            table_hints=_as_list(data.get("table_hints")),
            examples=_as_list(data.get("examples")),
            priority=_as_int(data.get("priority")),
        )

    def matches(self, value: str) -> bool:
        """忽略大小写判断输入设备类型是否命中标准类型或别名。"""
        normalized = _casefold_text(value)
        return bool(normalized) and normalized in {
            _casefold_text(item) for item in self.device_types + self.aliases
        }

    def to_dict(self) -> Dict[str, Any]:
        """将设备能力规格转换为紧凑字典。"""
        return _compact_dict(asdict(self))


@dataclass
class SpecialCapabilitySpec:
    """告警、链路、资源和关系等六类骨架之外的特殊能力。"""

    capability_id: str = ""
    capability_type: str = ""
    domain: str = ""
    device_types: List[str] = field(default_factory=list)
    objects: List[str] = field(default_factory=list)
    trigger_terms: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    table_hints: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "SpecialCapabilitySpec":
        """从配置字典构造特殊能力规格。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            capability_id=str(data.get("capability_id", "") or "").strip(),
            capability_type=str(data.get("capability_type", "") or "").strip(),
            domain=str(data.get("domain", "") or "").strip(),
            device_types=_as_list(data.get("device_types")),
            objects=_as_list(data.get("objects")),
            trigger_terms=_as_list(data.get("trigger_terms")),
            properties=_as_list(data.get("properties")),
            table_hints=_as_list(data.get("table_hints")),
            examples=_as_list(data.get("examples")),
            priority=_as_int(data.get("priority")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """将特殊能力规格转换为紧凑字典。"""
        return _compact_dict(asdict(self))


@dataclass
class CapabilityCandidate:
    """由设备规格和查询骨架动态生成的最终候选能力。"""

    capability_id: str = ""
    capability_type: str = ""
    domain: str = ""
    device_types: List[str] = field(default_factory=list)
    subcomponent_types: List[str] = field(default_factory=list)
    objects: List[str] = field(default_factory=list)
    locators: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    table_hints: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    priority: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """将动态候选能力转换为 Prompt 输入字典。"""
        return _compact_dict(asdict(self))


def _as_int(value: Any) -> int:
    """将任意值安全转换为整数，失败时返回零。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
