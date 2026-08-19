"""最小化上下文 + 六类能力召回 + LLM 表达的问数推荐调用器。"""

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .capabilities import recommend_capabilities
from .capability_loader import load_capability_cards
from .config import EXPLAIN_FIELD, LLM_CHAT_CALL_ERROR_REASON, RECOMMENDS_FIELD
from .field_analysis import analyze_candidate_fields
from .logical_model_reader import load_metadata_tables
from .models import DeviceCapabilityProfile, MetadataTable, RecommendationContext
from .prompt import QUESTION_RECOMMENDATION_USER_TEMPLATE, _build_system_prompt
from .simplify_analysis import analyze_simplify_constraints


class QuestionRecommendationError(Exception):
    """问数推荐异常。"""


def recommend_questions_chat(
    context: Any,
    llm_chat_client: Callable[[List[Dict[str, str]]], str],
    logical_model_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    根据标准化 RecommendationContext 生成推荐问题。

    推荐器自动加载内置设备能力规格，执行确定性过滤和 Top 12 排序，再将候选能力交给
    Chat LLM 自然化表达。LLM 返回结构合法后，只删除仍包含确定性 unsupported 字段的
    推荐项；除此之外不做补足或改写。
    """
    normalized_context = _normalize_context(context)
    metadata_tables = (
        load_metadata_tables(normalized_context.tables, logical_model_dir)
        if normalized_context.tables and logical_model_dir
        else []
    )
    domain_cards, special_cards = load_capability_cards(logical_model_dir)
    candidate_capabilities = recommend_capabilities(
        normalized_context,
        metadata_tables=metadata_tables,
        domain_cards=domain_cards,
        special_cards=special_cards,
        limit=12,
    )
    candidate_payload = [item.to_dict() for item in candidate_capabilities]
    field_analysis = analyze_candidate_fields(
        normalized_context, candidate_payload, metadata_tables, domain_cards
    )
    simplify_analysis = analyze_simplify_constraints(normalized_context)
    messages = _build_chat_messages(
        normalized_context,
        metadata_tables,
        candidate_payload,
        domain_cards,
        field_analysis=field_analysis,
        simplify_analysis=simplify_analysis,
    )

    try:
        llm_response = llm_chat_client(messages)
    except Exception as exc:
        raise QuestionRecommendationError(f"{LLM_CHAT_CALL_ERROR_REASON}: {exc}")

    parsed = _parse_llm_response(llm_response)
    if parsed:
        return _filter_unsupported_field_recommends(parsed, field_analysis)
    return parsed or {RECOMMENDS_FIELD: [], EXPLAIN_FIELD: ""}


def _build_chat_messages(
    context: RecommendationContext,
    metadata_tables: Sequence[MetadataTable],
    candidate_capabilities: Sequence[Mapping[str, Any]],
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    field_analysis: Optional[Mapping[str, Sequence[str]]] = None,
    simplify_analysis: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, str]]:
    """将标准上下文、按表元数据和候选能力组装为 Chat API messages。"""
    if field_analysis is None:
        field_analysis = analyze_candidate_fields(
            context, candidate_capabilities, metadata_tables, domain_cards
        )
    if simplify_analysis is None:
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


def _filter_unsupported_field_recommends(
    result: Dict[str, Any],
    field_analysis: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    """删除仍包含不支持属性或指标原文的推荐问题。"""
    unsupported_fields = _unsupported_fields(field_analysis)
    if not unsupported_fields:
        return result

    recommends = result[RECOMMENDS_FIELD]
    filtered = [
        item for item in recommends if not _contains_unsupported_field(item, unsupported_fields)
    ]
    if len(filtered) == len(recommends):
        return result
    explain = result[EXPLAIN_FIELD] if filtered else ""
    return {RECOMMENDS_FIELD: filtered, EXPLAIN_FIELD: explain}


def _unsupported_fields(field_analysis: Mapping[str, Sequence[str]]) -> List[str]:
    """按稳定顺序汇总当前不支持的属性和指标名称。"""
    result: List[str] = []
    for key in ("unsupported_properties", "unsupported_kpis"):
        values = field_analysis.get(key, [])
        for value in values:
            field = str(value or "").strip()
            if field:
                result.append(field)
    return result


def _contains_unsupported_field(text: str, unsupported_fields: Sequence[str]) -> bool:
    """判断推荐问题中是否仍包含 unsupported 字段原文。"""
    normalized_text = _match_text(text)
    for field in unsupported_fields:
        normalized_field = _match_text(field)
        if normalized_field and normalized_field in normalized_text:
            return True
    return False


def _match_text(value: Any) -> str:
    """生成推荐正文过滤使用的大小写无关匹配文本。"""
    return str(value or "").strip().casefold()


def _normalize_context(value: Any) -> RecommendationContext:
    """接受标准上下文实例或兼容字典，其他输入类型直接拒绝。"""
    if isinstance(value, RecommendationContext):
        return value
    if isinstance(value, Mapping):
        return RecommendationContext.from_dict(value)
    raise TypeError("context 必须是 RecommendationContext 或兼容字典")


def _json_dumps(value: Any) -> str:
    """将 Prompt 输入序列化为缩进 JSON，并保留中文字符。"""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
