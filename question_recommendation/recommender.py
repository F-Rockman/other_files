"""
结构化模板 + LLM 表达的问数推荐调用器。
"""

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .config import (
    EXPLAIN_FIELD,
    LLM_CHAT_CALL_ERROR_REASON,
    RECOMMENDS_FIELD,
)
from .metadata_loader import PathProvider, load_logical_metadata
from .models import MetadataColumn, RecognizedIntent, StructuredTemplate
from .prompt import QUESTION_RECOMMENDATION_SYSTEM_PROMPT, QUESTION_RECOMMENDATION_USER_TEMPLATE


class QuestionRecommendationError(Exception):
    """问数推荐异常。"""


def recommend_questions_chat(
    user_question: str,
    llm_chat_client: Callable[[List[Dict[str, str]]], str],
    scene_type: str = "error",
    intercept_reason: str = "",
    intercept_detail: str = "",
    recognized_intent: Optional[Any] = None,
    candidate_templates: Optional[Sequence[Any]] = None,
    logical_model_path_provider: Optional[PathProvider] = None,
    business_info: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    生成推荐问题（Chat API 风格）。

    参数:
        user_question:
            用户原始问题。用于保留原始表达、查询条件和值；必填。
        llm_chat_client:
            接收 ``[{"role": "system", ...}, {"role": "user", ...}]`` 消息列表并
            返回响应字符串的 Chat LLM 客户端。
        scene_type:
            ``error`` 或 ``normal``。默认按失败恢复场景处理。
        intercept_reason:
            失败或拒答原因文本。error 场景建议填写，用于识别恢复类型和异常值。
        intercept_detail:
            失败补充信息。可用于表达更具体的失败范围或限制。
        recognized_intent:
            ``RecognizedIntent`` 或兼容字典。它是锁定意图、业务域和对象的最高优先级输入。
        candidate_templates:
            ``StructuredTemplate`` 或兼容字典列表。通常传入外部召回后的 Top 15 模板。
        logical_model_path_provider:
            返回逻辑模型文件目录的可调用方法。推荐器根据 ``recognized_intent.tables``
            读取 ``{table_name}.logical.yaml`` 并按表组织元数据。
        business_info:
            额外业务范围或限制信息。允许为空。

    返回:
        dict: {"recommends": [str, ...], "explain": str}
    """
    context = _build_context(
        user_question=user_question,
        scene_type=scene_type,
        intercept_reason=intercept_reason,
        intercept_detail=intercept_detail,
        recognized_intent=recognized_intent,
        candidate_templates=candidate_templates,
        logical_model_path_provider=logical_model_path_provider,
        business_info=business_info,
    )
    messages = _build_chat_messages(context)

    try:
        llm_response = llm_chat_client(messages)
    except Exception as exc:
        raise QuestionRecommendationError(f"{LLM_CHAT_CALL_ERROR_REASON}: {exc}")

    parsed = _parse_llm_response(llm_response)
    return parsed or {RECOMMENDS_FIELD: [], EXPLAIN_FIELD: ""}


def _build_context(
    user_question: str,
    scene_type: str,
    intercept_reason: str,
    intercept_detail: str,
    recognized_intent: Optional[Any],
    candidate_templates: Optional[Sequence[Any]],
    logical_model_path_provider: Optional[PathProvider],
    business_info: Optional[Any],
) -> Dict[str, Any]:
    intent = _normalize_intent(recognized_intent)
    templates = _normalize_templates(candidate_templates or [])
    metadata = (
        load_logical_metadata(intent.tables, logical_model_path_provider)
        if intent.tables and logical_model_path_provider
        else []
    )
    return {
        "user_question": user_question or "",
        "scene_type": scene_type or "error",
        "intercept_reason": intercept_reason or "",
        "intercept_detail": intercept_detail or "",
        "recognized_intent": intent,
        "candidate_templates": templates,
        "metadata_tables": _group_metadata_by_table(metadata),
        "business_info": business_info if business_info is not None else {},
    }


def _build_chat_messages(context: Mapping[str, Any]) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": QUESTION_RECOMMENDATION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(context)},
    ]


def _build_user_prompt(context: Mapping[str, Any]) -> str:
    return QUESTION_RECOMMENDATION_USER_TEMPLATE.format(
        user_question=context["user_question"],
        scene_type=context["scene_type"],
        intercept_reason=context["intercept_reason"],
        intercept_detail=context["intercept_detail"],
        recognized_intent_json=_json_dumps(context["recognized_intent"].to_dict()),
        candidate_templates_json=_json_dumps([item.to_dict() for item in context["candidate_templates"]]),
        metadata_tables_json=_json_dumps(context["metadata_tables"]),
        business_info_json=_json_dumps(context["business_info"]),
    )


def _parse_llm_response(llm_response: str) -> Optional[Dict[str, Any]]:
    """
    解析 LLM 输出。

    支持纯 JSON、Markdown 代码块 JSON、带额外解释文本的 JSON。
    返回 None 表示无法解析或结构不合法。
    """
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
    patterns = [
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return None


def _coerce_result(parsed: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(parsed, dict):
        return None

    recommends = parsed.get(RECOMMENDS_FIELD)
    explain = parsed.get(EXPLAIN_FIELD)
    if not isinstance(recommends, list) or not all(isinstance(item, str) for item in recommends):
        return None
    if not isinstance(explain, str):
        return None

    return {RECOMMENDS_FIELD: recommends, EXPLAIN_FIELD: explain}


def _normalize_intent(value: Any) -> RecognizedIntent:
    if isinstance(value, RecognizedIntent):
        return value
    if isinstance(value, Mapping):
        return RecognizedIntent.from_dict(value)
    return RecognizedIntent()


def _normalize_templates(values: Sequence[Any]) -> List[StructuredTemplate]:
    result = []
    for item in values:
        if isinstance(item, StructuredTemplate):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(StructuredTemplate.from_dict(item))
    return result


def _group_metadata_by_table(values: Sequence[MetadataColumn]) -> List[Dict[str, Any]]:
    """将平铺列元数据按表名和表描述聚合，避免多张表的列混在一起。"""
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in values:
        key = (item.table_name, item.table_description)
        if key not in grouped:
            grouped[key] = {
                "table_name": item.table_name,
                "table_description": item.table_description,
                "columns": [],
            }
        column = {
            "column_name": item.column_name,
            "column_description": item.column_description,
        }
        grouped[key]["columns"].append(
            {name: value for name, value in column.items() if value not in (None, "", [], {})}
        )
    return [
        {name: value for name, value in table.items() if value not in (None, "", [], {})}
        for table in grouped.values()
    ]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)

