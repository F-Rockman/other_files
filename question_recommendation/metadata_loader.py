"""
从逻辑模型 YAML 文件加载推荐所需的表列业务描述。
"""

from importlib import import_module
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Union

from .models import MetadataColumn


class LogicalMetadataError(Exception):
    """逻辑模型元数据加载异常。"""


PathProvider = Callable[[], Union[str, Path]]


def load_logical_metadata(
    table_names: Sequence[str],
    logical_model_path_provider: PathProvider,
) -> List[MetadataColumn]:
    """
    根据逻辑表名加载 ``{table_name}.logical.yaml``。

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
    result: List[MetadataColumn] = []
    for table_name in _dedupe_table_names(table_names):
        file_path = _logical_file_path(base_dir, table_name)
        if file_path is None or not file_path.is_file():
            continue
        try:
            with file_path.open("r", encoding="utf-8") as file:
                document = yaml.safe_load(file)
        except Exception:
            continue
        result.extend(_extract_metadata_columns(document))
    return result


def _load_yaml_module():
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


def _extract_metadata_columns(document: Any) -> List[MetadataColumn]:
    """从逻辑模型文档提取表名、表描述、列名和列描述。"""
    if not isinstance(document, Mapping):
        return []

    table_name = _text(document.get("name"))
    table_description = _text(document.get("description_cn"))
    schema = document.get("schema")
    if not table_name or not isinstance(schema, Mapping):
        return []

    fields = schema.get("fields")
    if not isinstance(fields, list):
        return []

    result: List[MetadataColumn] = []
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        column_name = _text(field.get("name"))
        if not column_name:
            continue
        result.append(
            MetadataColumn(
                table_name=table_name,
                table_description=table_description,
                column_name=column_name,
                column_description=_text(field.get("description_cn")),
            )
        )
    return result


def _dedupe_table_names(table_names: Sequence[str]) -> List[str]:
    seen = set()
    result = []
    for item in table_names or []:
        name = _text(item)
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
