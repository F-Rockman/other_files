"""
从逻辑模型 YAML 文件加载推荐所需的表列业务描述。
"""

from importlib import import_module
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Union

from .models import MetadataColumn, MetadataTable


class LogicalMetadataError(Exception):
    """逻辑模型元数据加载异常。"""


PathProvider = Callable[[], Union[str, Path]]


def load_logical_metadata(
    table_names: Sequence[str],
    logical_model_path_provider: PathProvider,
) -> List[MetadataTable]:
    """
    根据逻辑表名加载 ``{table_name}.logical.yaml``。

    返回值在加载阶段就按表组织为 ``List[MetadataTable]``，每个逻辑模型文件对应
    一个 ``MetadataTable``，字段位于其 ``columns`` 中。

    路径提供方法返回存放所有逻辑模型文件的目录。单个文件不存在、无法读取或内容
    不符合预期结构时会跳过；缺少 PyYAML 或路径提供方法返回无效目录时抛出
    ``LogicalMetadataError``。
    """
    if not callable(logical_model_path_provider):
        raise LogicalMetadataError("logical_model_path_provider 必须是可调用方法")

    try:
        base_dir = Path(logical_model_path_provider()).expanduser().resolve()
    except Exception as exc:
        raise LogicalMetadataError(f"获取逻辑模型目录失败: {exc}") from exc

    if not base_dir.is_dir():
        raise LogicalMetadataError(f"逻辑模型目录不存在或不是目录: {base_dir}")

    yaml = _load_yaml_module()
    result: List[MetadataTable] = []
    for table_name in _dedupe_table_names(table_names):
        file_path = _logical_file_path(base_dir, table_name)
        if file_path is None or not file_path.is_file():
            continue
        try:
            with file_path.open("r", encoding="utf-8") as file:
                document = yaml.safe_load(file)
        except Exception:
            continue
        metadata_table = _extract_metadata_table(document)
        if metadata_table is not None:
            result.append(metadata_table)
    return result


def _load_yaml_module():
    """延迟导入 PyYAML，并将依赖缺失转换为模块领域异常。"""
    try:
        return import_module("yaml")
    except ModuleNotFoundError as exc:
        raise LogicalMetadataError(
            "读取 .logical.yaml 需要 PyYAML，请先安装: pip install PyYAML"
        ) from exc


def _logical_file_path(base_dir: Path, table_name: str) -> Optional[Path]:
    """构造安全的逻辑模型文件路径，拒绝路径穿越和子目录表名。"""
    if not table_name or Path(table_name).name != table_name:
        return None
    candidate = (base_dir / f"{table_name}.logical.yaml").resolve()
    if candidate.parent != base_dir:
        return None
    return candidate


def _extract_metadata_table(document: Any) -> Optional[MetadataTable]:
    """从一个逻辑模型文档提取已按表组织的业务元数据。"""
    if not isinstance(document, Mapping):
        return None

    table_name = _text(document.get("name"))
    table_description = _text(document.get("description_cn"))
    schema = document.get("schema")
    if not table_name or not isinstance(schema, Mapping):
        return None

    fields = schema.get("fields")
    if not isinstance(fields, list):
        return None

    columns: List[MetadataColumn] = []
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        column_name = _text(field.get("name"))
        if not column_name:
            continue
        columns.append(
            MetadataColumn(
                column_name=column_name,
                column_description=_text(field.get("description_cn")),
            )
        )
    return MetadataTable(
        table_name=table_name,
        table_description=table_description,
        columns=columns,
    )


def _dedupe_table_names(table_names: Sequence[str]) -> List[str]:
    """按输入顺序清理并去重逻辑表名。"""
    seen = set()
    result = []
    for item in table_names or []:
        name = _text(item)
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _text(value: Any) -> str:
    """将任意值转换为去除首尾空白的文本，空值转换为空字符串。"""
    return "" if value is None else str(value).strip()
