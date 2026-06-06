"""最小化上下文 + 能力卡召回 + LLM 表达的问数推荐调用器。"""

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .capabilities import recommend_capabilities
from .config import EXPLAIN_FIELD, LLM_CHAT_CALL_ERROR_REASON, RECOMMENDS_FIELD
from .metadata_loader import PathProvider, load_logical_metadata
from .models import MetadataTable, RecommendationContext
from .prompt import QUESTION_RECOMMENDATION_SYSTEM_PROMPT, QUESTION_RECOMMENDATION_USER_TEMPLATE


class QuestionRecommendationError(Exception):
    """问数推荐异常。"""


def recommend_questions_chat(
    context: Any,
    llm_chat_client: Callable[[List[Dict[str, str]]], str],
    logical_model_path_provider: Optional[PathProvider] = None,
) -> Dict[str, Any]:
    """
    根据标准化 RecommendationContext 生成推荐问题。

    推荐器自动加载内置能力卡，执行确定性过滤和 Top 12 排序，再将候选能力交给
    Chat LLM 自然化表达。LLM 返回结构合法时直接返回，不做内容过滤或补足。
    """
    normalized_context = _normalize_context(context)
    metadata_tables = (
        load_logical_metadata(normalized_context.tables, logical_model_path_provider)
        if normalized_context.tables and logical_model_path_provider
        else []
    )
    candidate_capabilities = recommend_capabilities(
        normalized_context,
        metadata_tables=metadata_tables,
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
    user_prompt = QUESTION_RECOMMENDATION_USER_TEMPLATE.format(
        recommendation_context_json=_json_dumps(context.to_dict()),
        candidate_capabilities_json=_json_dumps(candidate_capabilities),
        metadata_tables_json=_json_dumps([table.to_dict() for table in metadata_tables]),
    )
    return [
        {"role": "system", "content": QUESTION_RECOMMENDATION_SYSTEM_PROMPT},
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
    if isinstance(value, RecommendationContext):
        return value
    if isinstance(value, Mapping):
        return RecommendationContext.from_dict(value)
    raise TypeError("context 必须是 RecommendationContext 或兼容字典")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
