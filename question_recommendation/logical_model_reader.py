"""逻辑模型 YAML 读取与用户可见字段过滤。"""

import json
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from .models import MetadataColumn, MetadataTable


def load_logical_model_document(
    logical_model_dir: str,
    table_name: str,
) -> Optional[Mapping[str, Any]]:
    """从逻辑模型目录安全读取单个 ``{table_name}.logical.yaml`` 文档。"""
    base_dir = _logical_model_base_dir(logical_model_dir)
    if base_dir is None:
        return None
    file_path = _logical_file_path(base_dir, table_name)
    if file_path is None or not file_path.is_file():
        return None
    return _load_yaml_document(file_path)


def load_metadata_tables(
    table_names: Sequence[Any],
    logical_model_dir: str,
) -> List[MetadataTable]:
    """按表名加载实时元数据，并过滤不对用户展示的字段。"""
    result: List[MetadataTable] = []
    for table_name in dedupe_texts(table_names):
        document = load_logical_model_document(logical_model_dir, table_name)
        metadata_table = metadata_table_from_document(document)
        if metadata_table is not None:
            result.append(metadata_table)
    return result


def metadata_table_from_document(document: Any) -> Optional[MetadataTable]:
    """从逻辑模型文档提取推荐 Prompt 使用的表列描述。"""
    if not isinstance(document, Mapping):
        return None
    table_name = clean_text(document.get("name"))
    if not table_name:
        return None
    return MetadataTable(
        table_name=table_name,
        table_description=clean_text(document.get("description_cn")),
        columns=_metadata_columns_from_fields(visible_fields(document)),
    )


def business_names_from_document(document: Any) -> List[str]:
    """从可展示字段中提取非空 ``businessName_cn``。"""
    result: List[str] = []
    for field in visible_fields(document):
        result.append(clean_text(field.get("businessName_cn")))
    return dedupe_texts(result)


def visible_fields(document: Any) -> List[Mapping[str, Any]]:
    """返回逻辑模型中对用户可见的字段列表。"""
    fields = _schema_fields(document)
    result: List[Mapping[str, Any]] = []
    for field in fields:
        if isinstance(field, Mapping) and not _is_hidden_field(field):
            result.append(field)
    return result


def dedupe_texts(values: Sequence[Any]) -> List[str]:
    """按输入顺序清理并去重文本。"""
    result: List[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def clean_text(value: Any) -> str:
    """将任意值转换为去除首尾空白的文本。"""
    return "" if value is None else str(value).strip()


def _logical_model_base_dir(logical_model_dir: str) -> Optional[Path]:
    """将目录字符串转换为逻辑模型目录；无效路径按无数据处理。"""
    if not logical_model_dir:
        return None
    base_dir = Path(logical_model_dir).expanduser().resolve()
    return base_dir if base_dir.is_dir() else None


def _logical_file_path(base_dir: Path, table_name: str) -> Optional[Path]:
    """构造安全的逻辑模型文件路径，拒绝路径穿越和子目录表名。"""
    if not table_name or Path(table_name).name != table_name:
        return None
    candidate = (base_dir / f"{table_name}.logical.yaml").resolve()
    if candidate.parent != base_dir:
        return None
    return candidate


def _load_yaml_document(file_path: Path) -> Optional[Mapping[str, Any]]:
    """读取 YAML 文档，无法解析或非对象文档时返回 None。"""
    try:
        import yaml

        with file_path.open("r", encoding="utf-8") as file:
            document = yaml.safe_load(file)
    except Exception:
        return None
    return document if isinstance(document, Mapping) else None


def _schema_fields(document: Any) -> List[Any]:
    """从逻辑模型文档中读取 schema.fields 列表。"""
    if not isinstance(document, Mapping):
        return []
    schema = document.get("schema")
    if not isinstance(schema, Mapping):
        return []
    fields = schema.get("fields")
    return fields if isinstance(fields, list) else []


def _is_hidden_field(field: Mapping[str, Any]) -> bool:
    """判断字段是否明确标记为 UI 不展示。"""
    properties = field.get("properties")
    if not isinstance(properties, Mapping):
        return False
    ui = properties.get("ui")
    if not isinstance(ui, str):
        return False
    ui_config = _parse_ui_config(ui)
    if not isinstance(ui_config, Mapping):
        return False
    return clean_text(ui_config.get("displayPriority")) == "never"


def _parse_ui_config(ui: str) -> Any:
    """解析字段 properties.ui JSON 字符串，异常时按无配置处理。"""
    try:
        return json.loads(ui)
    except Exception:
        return None


def _metadata_columns_from_fields(fields: Sequence[Mapping[str, Any]]) -> List[MetadataColumn]:
    """将可展示字段转换为 Prompt 使用的列元数据。"""
    columns: List[MetadataColumn] = []
    for field in fields:
        column_name = clean_text(field.get("name"))
        if column_name:
            columns.append(
                MetadataColumn(
                    column_name=column_name,
                    column_description=clean_text(field.get("description_cn")),
                )
            )
    return columns
