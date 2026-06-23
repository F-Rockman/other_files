"""最小化上下文 + 六类能力召回 + LLM 表达的问数推荐调用器。"""

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .capabilities import recommend_capabilities
from .config import EXPLAIN_FIELD, LLM_CHAT_CALL_ERROR_REASON, RECOMMENDS_FIELD
from .field_analysis import analyze_candidate_fields
from .models import MetadataColumn, MetadataTable, RecommendationContext
from .prompt import QUESTION_RECOMMENDATION_USER_TEMPLATE, _build_system_prompt
from .simplify_analysis import analyze_simplify_constraints


class QuestionRecommendationError(Exception):
    """问数推荐异常。"""


def recommend_questions_chat(
    context: Any,
    llm_chat_client: Callable[[List[Dict[str, str]]], str],
    logical_model_path_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    根据标准化 RecommendationContext 生成推荐问题。

    推荐器自动加载内置设备能力规格，执行确定性过滤和 Top 12 排序，再将候选能力交给
    Chat LLM 自然化表达。LLM 返回结构合法时直接返回，不做内容过滤或补足。
    """
    normalized_context = _normalize_context(context)
    metadata_tables = (
        _load_logical_metadata(normalized_context.tables, logical_model_path_provider)
        if normalized_context.tables and logical_model_path_provider
        else []
    )
    candidate_capabilities = recommend_capabilities(
        normalized_context,
        metadata_tables=metadata_tables,
        logical_model_path_provider=logical_model_path_provider,
        limit=12,
    )
    messages = _build_chat_messages(
        normalized_context,
        metadata_tables,
        [item.to_dict() for item in candidate_capabilities],
    )

    try:
        llm_response = llm_chat_client(messages)
    except Exception as exc:
        raise QuestionRecommendationError(f"{LLM_CHAT_CALL_ERROR_REASON}: {exc}")

    parsed = _parse_llm_response(llm_response)
    return parsed or {RECOMMENDS_FIELD: [], EXPLAIN_FIELD: ""}


def _build_chat_messages(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable],
    candidate_capabilities: Sequence[Mapping[str, Any]],
) -> List[Dict[str, str]]:
    """将标准上下文、按表元数据和候选能力组装为 Chat API messages。"""
    field_analysis = analyze_candidate_fields(
        context, candidate_capabilities, metadata_tables
    )
    simplify_analysis = analyze_simplify_constraints(context)
    user_prompt = QUESTION_RECOMMENDATION_USER_TEMPLATE.format(
        recommendation_context_json=_json_dumps(context.to_dict()),
        candidate_capabilities_json=_json_dumps(candidate_capabilities),
        metadata_tables_json=_json_dumps([table.to_dict() for table in metadata_tables]),
    )
    user_prompt += (
        "\n\n确定性候选字段分析 candidate_field_analysis：\n"
        + _json_dumps(field_analysis)
    )
    user_prompt += (
        "\n\n确定性简化分析 simplify_analysis：\n"
        + _json_dumps(simplify_analysis)
    )
    system_prompt = _build_system_prompt(context, metadata_tables)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_llm_response(llm_response: str) -> Optional[Dict[str, Any]]:
    """解析纯 JSON、Markdown JSON 或带额外文本的 JSON。"""
    if not llm_response:
        return None

    candidates = [llm_response.strip()]
    json_block = _extract_json_block(llm_response)
    if json_block:
        candidates.insert(0, json_block)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        result = _coerce_result(parsed)
        if result is not None:
            return result
    return None


def _extract_json_block(text: str) -> Optional[str]:
    """从 Markdown 代码块或带额外文本的响应中提取首个 JSON 对象文本。"""
    patterns = [
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0).strip() if match else None


def _coerce_result(parsed: Any) -> Optional[Dict[str, Any]]:
    """校验解析结果是否符合 ``recommends: list[str]`` 与 ``explain: str`` 结构。"""
    if not isinstance(parsed, dict):
        return None
    recommends = parsed.get(RECOMMENDS_FIELD)
    explain = parsed.get(EXPLAIN_FIELD)
    if not isinstance(recommends, list) or not all(isinstance(item, str) for item in recommends):
        return None
    if not isinstance(explain, str):
        return None
    return {RECOMMENDS_FIELD: recommends, EXPLAIN_FIELD: explain}


def _normalize_context(value: Any) -> RecommendationContext:
    """接受标准上下文实例或兼容字典，其他输入类型直接拒绝。"""
    if isinstance(value, RecommendationContext):
        return value
    if isinstance(value, Mapping):
        return RecommendationContext.from_dict(value)
    raise TypeError("context 必须是 RecommendationContext 或兼容字典")


def _load_logical_metadata(
    table_names: Sequence[str],
    logical_model_path: str,
) -> List[MetadataTable]:
    """按逻辑表名从目录字符串下读取 ``{table}.logical.yaml``。"""
    base_dir = _logical_model_base_dir(logical_model_path)
    if base_dir is None:
        return []
    result: List[MetadataTable] = []
    for table_name in _dedupe_texts(table_names):
        file_path = _logical_file_path(base_dir, table_name)
        if file_path is None or not file_path.is_file():
            continue
        metadata_table = _load_metadata_table(file_path)
        if metadata_table is not None:
            result.append(metadata_table)
    return result


def _logical_model_base_dir(logical_model_path: str) -> Optional[Path]:
    """将目录字符串转换为逻辑模型目录；无效路径按无元数据处理。"""
    if not logical_model_path:
        return None
    base_dir = Path(logical_model_path).expanduser().resolve()
    return base_dir if base_dir.is_dir() else None


def _logical_file_path(base_dir: Path, table_name: str) -> Optional[Path]:
    """构造安全的逻辑模型文件路径，拒绝路径穿越和子目录表名。"""
    if not table_name or Path(table_name).name != table_name:
        return None
    candidate = (base_dir / f"{table_name}.logical.yaml").resolve()
    if candidate.parent != base_dir:
        return None
    return candidate


def _load_metadata_table(file_path: Path) -> Optional[MetadataTable]:
    """读取单个逻辑模型文件并转换为按表组织的元数据。"""
    try:
        import yaml

        with file_path.open("r", encoding="utf-8") as file:
            document = yaml.safe_load(file)
    except Exception:
        return None
    return _extract_metadata_table(document)


def _extract_metadata_table(document: Any) -> Optional[MetadataTable]:
    """从逻辑模型文档提取推荐 Prompt 使用的表列描述。"""
    if not isinstance(document, Mapping):
        return None
    table_name = _text(document.get("name"))
    schema = document.get("schema")
    if not table_name or not isinstance(schema, Mapping):
        return None
    fields = schema.get("fields")
    if not isinstance(fields, list):
        return None
    return MetadataTable(
        table_name=table_name,
        table_description=_text(document.get("description_cn")),
        columns=_extract_metadata_columns(fields),
    )


def _extract_metadata_columns(fields: Sequence[Any]) -> List[MetadataColumn]:
    """从字段列表提取物理列名和列描述，保持既有 Prompt 输入结构。"""
    columns: List[MetadataColumn] = []
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        column_name = _text(field.get("name"))
        if column_name:
            columns.append(
                MetadataColumn(
                    column_name=column_name,
                    column_description=_text(field.get("description_cn")),
                )
            )
    return columns


def _dedupe_texts(values: Sequence[Any]) -> List[str]:
    """按输入顺序清理并去重文本。"""
    result: List[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _text(value: Any) -> str:
    """将任意值转换为去除首尾空白的文本。"""
    return "" if value is None else str(value).strip()


def _json_dumps(value: Any) -> str:
    """将 Prompt 输入序列化为缩进 JSON，并保留中文字符。"""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
