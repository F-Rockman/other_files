"""
结构化模板 + LLM 表达的问数推荐调用器。
"""

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import (
    BASIC_TEMPLATE_TYPES,
    DEFAULT_FALLBACK_EXPLAIN,
    DEFAULT_RECOMMENDATION_COUNT,
    EMPTY_FALLBACK_EXPLAIN,
    EXPLAIN_FIELD,
    GENERIC_SLOT_LABELS,
    LLM_CHAT_CALL_ERROR_REASON,
    MAX_EXPLAIN_LENGTH,
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
    return _finalize_result(parsed, context)


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
    返回 None 表示无法解析或结构不合法，由上层走兜底。
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

    raw_recommends = parsed.get(RECOMMENDS_FIELD)
    if raw_recommends is None and "recommendations" in parsed:
        raw_recommends = parsed.get("recommendations")

    recommends = _coerce_recommendation_list(raw_recommends)
    if recommends is None:
        return None

    explain = parsed.get(EXPLAIN_FIELD, "")
    if not isinstance(explain, str):
        explain = str(explain)

    return {
        RECOMMENDS_FIELD: recommends,
        EXPLAIN_FIELD: _truncate_explain(explain.strip()),
    }


def _coerce_recommendation_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None

    result: List[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("question") or item.get("text") or "").strip()
        else:
            text = ""
        if text:
            result.append(text)
    return result


def _finalize_result(parsed: Optional[Dict[str, Any]], context: Mapping[str, Any]) -> Dict[str, Any]:
    invalid_values = _extract_invalid_values(
        context.get("intercept_reason", ""),
        context.get("intercept_detail", ""),
    )
    recommends = []
    if parsed:
        recommends = _sanitize_recommends(parsed.get(RECOMMENDS_FIELD, []), invalid_values)

    if len(recommends) < DEFAULT_RECOMMENDATION_COUNT:
        fallback = _fallback_recommendations(context, invalid_values, excludes=recommends)
        recommends.extend(fallback)

    recommends = _dedupe(recommends)[:DEFAULT_RECOMMENDATION_COUNT]

    explain = ""
    if parsed:
        explain = str(parsed.get(EXPLAIN_FIELD, "")).strip()
    if not explain:
        explain = DEFAULT_FALLBACK_EXPLAIN if recommends else EMPTY_FALLBACK_EXPLAIN

    return {
        RECOMMENDS_FIELD: recommends,
        EXPLAIN_FIELD: _truncate_explain(explain),
    }


def _sanitize_recommends(recommends: Iterable[str], invalid_values: Sequence[str]) -> List[str]:
    sanitized = []
    for question in recommends:
        text = str(question).strip()
        if not text:
            continue
        if _contains_invalid_value(text, invalid_values):
            continue
        if _has_forbidden_enum(text):
            continue
        sanitized.append(text)
    return _dedupe(sanitized)


def _fallback_recommendations(
    context: Mapping[str, Any],
    invalid_values: Sequence[str],
    excludes: Optional[Sequence[str]] = None,
) -> List[str]:
    excludes = list(excludes or [])
    templates: List[StructuredTemplate] = list(context.get("candidate_templates") or [])
    if not templates:
        return _generic_fallback_recommendations(context, invalid_values, excludes)

    scored = [_score_template(template, context) for template in templates]
    has_positive_match = any(item[1] for item in scored)
    if has_positive_match:
        scored = [item for item in scored if item[1]]
    scored.sort(key=lambda item: item[0], reverse=True)

    result: List[str] = []
    for _, _, template in scored:
        question = _render_template_question(template, context, invalid_values)
        if not question:
            continue
        if question in excludes or question in result:
            continue
        if _contains_invalid_value(question, invalid_values):
            continue
        if _has_forbidden_enum(question):
            continue
        result.append(question)
        if len(result) >= DEFAULT_RECOMMENDATION_COUNT - len(excludes):
            return result

    if len(result) < DEFAULT_RECOMMENDATION_COUNT - len(excludes):
        result.extend(_generic_fallback_recommendations(context, invalid_values, excludes + result))
    return _dedupe(result)[: DEFAULT_RECOMMENDATION_COUNT - len(excludes)]


def _score_template(template: StructuredTemplate, context: Mapping[str, Any]) -> Tuple[int, bool, StructuredTemplate]:
    blob = _context_blob(context)
    score = int(template.priority or 0)
    matched = False

    intent_type = context["recognized_intent"].intent_type
    if intent_type and intent_type in template.intent_tags:
        score += 80

    for tag in template.domain_tags:
        if tag and tag in blob:
            score += 60
            matched = True
    for tag in template.object_tags:
        if tag and tag in blob:
            score += 60
            matched = True
    for tag in (template.parent_object, template.child_object):
        if tag and tag in blob:
            score += 50
            matched = True
    for tag in template.supported_recovery_types:
        if tag and tag in blob:
            score += 25
            matched = True

    if template.template_type in BASIC_TEMPLATE_TYPES:
        score += 20
    return score, matched, template


def _render_template_question(
    template: StructuredTemplate,
    context: Mapping[str, Any],
    invalid_values: Sequence[str],
) -> str:
    text = (template.template_text or "").strip()
    if _should_synthesize(text):
        return _synthesize_question(template, context, invalid_values)

    locator = _locator_phrase(context, invalid_values, template)
    if locator:
        text = re.sub(r"\{定位方式\}", locator, text)
        text = re.sub(r"\{device_ip_or_name\}", locator, text)
        text = re.sub(r"\{设备定位\}", locator, text)

    text = re.sub(r"\{([^{}]+)\}", lambda match: _slot_to_placeholder(match.group(1)), text)
    text = _normalize_known_enums(text, template)
    if _should_synthesize(text):
        return _synthesize_question(template, context, invalid_values)
    return _cleanup_question(text)


def _should_synthesize(text: str) -> bool:
    if not text:
        return True
    if "/" in text:
        return True
    if re.search(r"\{[^{}]+\}", text):
        return False
    return False


def _normalize_known_enums(text: str, template: StructuredTemplate) -> str:
    object_label = _object_phrase(template)
    template_type = _template_type_label(template)
    replacements = {
        "IP地址/设备名称": "IP 为“IP地址”的设备",
        "设备名称/IP地址": "IP 为“IP地址”的设备",
        "OLT设备名称/IP地址": "IP 为“IP地址”的 OLT",
        "ONU设备名称/IP地址": "IP 为“IP地址”的 ONU",
        "列表/数量/TOPN": template_type,
        "平均值/最大值/最小值/趋势": template_type,
        "最高/最低/大于/小于": template_type,
        "接口/端口/单板/光模块/机框/远端模块": object_label,
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _synthesize_question(
    template: StructuredTemplate,
    context: Mapping[str, Any],
    invalid_values: Sequence[str],
) -> str:
    object_label = _object_phrase(template)
    locator = _locator_phrase(context, invalid_values, template)
    template_type = _template_type_label(template)

    subject = object_label
    if locator:
        subject = f"{locator}的{object_label}"

    if template_type == "数量":
        return _cleanup_question(f"查询{subject}数量")
    if template_type in {"告警", "告警列表", "告警数量", "告警分布"}:
        return _cleanup_question(f"查询{subject}告警列表")
    if template_type in {"趋势", "指标", "性能指标"}:
        metric = _first_text(context["recognized_intent"].metric_info) or "指标"
        return _cleanup_question(f"查询{subject}{metric}趋势")
    if template_type in {"TopN", "TOPN", "排行"}:
        return _cleanup_question(f"查询{subject}TopN")
    if template_type in {"基础信息", "详情"}:
        return _cleanup_question(f"查询{subject}基础信息")
    if template_type == "概览":
        return _cleanup_question(f"查询{subject}概览")
    return _cleanup_question(f"查询{subject}列表")


def _generic_fallback_recommendations(
    context: Mapping[str, Any],
    invalid_values: Sequence[str],
    excludes: Optional[Sequence[str]] = None,
) -> List[str]:
    excludes = list(excludes or [])
    intent = context["recognized_intent"]
    subject = (
        _first_text(intent.sub_component_info)
        or _first_text(intent.device_info)
        or _first_text(intent.subnet_info)
        or _first_text(intent.alarm_info)
    )
    domain = _first_text(intent.domain_info)
    if domain and subject and domain not in subject:
        subject = f"{domain}{subject}"
    if not subject:
        return []

    candidates = [
        f"查询{subject}列表",
        f"查询{subject}数量",
        f"查询{subject}基础信息",
    ]
    return [
        item
        for item in candidates
        if item not in excludes and not _contains_invalid_value(item, invalid_values)
    ]


def _object_phrase(template: StructuredTemplate) -> str:
    if template.parent_object and template.child_object:
        if template.child_object in template.parent_object:
            return template.parent_object
        return f"{template.parent_object}{template.child_object}"

    if template.child_object:
        domain = template.domain_tags[0] if template.domain_tags else ""
        if domain and domain not in template.child_object:
            return f"{domain}{template.child_object}"
        return template.child_object

    if template.object_tags:
        if len(template.object_tags) >= 2:
            parent, child = template.object_tags[0], template.object_tags[-1]
            if child in parent:
                return parent
            return f"{parent}{child}"
        return template.object_tags[0]

    if template.domain_tags:
        return f"{template.domain_tags[0]}设备"
    return "设备"


def _template_type_label(template: StructuredTemplate) -> str:
    if template.template_type:
        return template.template_type
    text = template.template_text or ""
    for item in ["基础信息", "详情", "数量", "列表", "告警", "趋势", "TopN", "TOPN", "概览"]:
        if item in text:
            return item
    return "列表"


def _locator_phrase(
    context: Mapping[str, Any],
    invalid_values: Sequence[str],
    template: Optional[StructuredTemplate] = None,
) -> str:
    if template is not None and not _needs_locator(template):
        return ""

    user_question = context.get("user_question", "")
    ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", user_question)
    if ip_match:
        ip = ip_match.group(0)
        if ip not in invalid_values:
            return f"IP 为 {ip}"
    return "IP 为“IP地址”"


def _needs_locator(template: StructuredTemplate) -> bool:
    text = template.template_text or ""
    locator_markers = ["IP", "名称", "定位", "device_ip", "device_name", "ip_or_name"]
    if any(marker in text for marker in locator_markers):
        return True
    return any(any(marker in slot for marker in locator_markers) for slot in template.slots)


def _slot_to_placeholder(slot_name: str) -> str:
    normalized = slot_name.strip()
    mapping = {
        "ip": "IP地址",
        "device_ip": "IP地址",
        "device_name": "设备名称",
        "name": "设备名称",
        "mac": "MAC地址",
        "interface_name": "接口名称",
        "port_name": "端口名称",
        "alarm_name": "告警名称",
        "time": "时间范围",
        "metric": "指标",
        "attribute": "属性",
    }
    label = mapping.get(normalized, normalized)
    if label.startswith("“") and label.endswith("”"):
        return label
    return f"“{label}”"


def _cleanup_question(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" 的", "的").replace(" ？", "？").replace(" ?", "？")
    text = text.replace("查询查询", "查询")
    return text


def _extract_invalid_values(*texts: str) -> List[str]:
    combined = "\n".join(item or "" for item in texts)
    values = set()
    for item in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", combined):
        values.add(item)
    for item in re.findall(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", combined):
        values.add(item)
    for item in re.findall(r"[“\"']([^”\"']{1,80})[”\"']", combined):
        item = item.strip()
        if item and item not in GENERIC_SLOT_LABELS:
            values.add(item)
    return sorted(values, key=len, reverse=True)


def _contains_invalid_value(text: str, invalid_values: Sequence[str]) -> bool:
    return any(value and value in text for value in invalid_values)


def _has_forbidden_enum(text: str) -> bool:
    if "/" not in text:
        return False
    enum_markers = [
        "IP地址",
        "设备名称",
        "列表",
        "数量",
        "TOPN",
        "平均值",
        "最大值",
        "接口",
        "端口",
        "光模块",
    ]
    return any(marker in text for marker in enum_markers)


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


def _context_blob(context: Mapping[str, Any]) -> str:
    data = {
        "user_question": context.get("user_question", ""),
        "intercept_reason": context.get("intercept_reason", ""),
        "intercept_detail": context.get("intercept_detail", ""),
        "recognized_intent": context["recognized_intent"].to_dict(),
        "business_info": context.get("business_info", {}),
    }
    return json.dumps(data, ensure_ascii=False, default=str)


def _first_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("name", "名称", "object", "对象", "type", "类型", "domain", "业务域"):
            if key in value and value[key]:
                return str(value[key]).strip()
        for item in value.values():
            text = _first_text(item)
            if text:
                return text
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        for item in value:
            text = _first_text(item)
            if text:
                return text
    return str(value).strip()


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _truncate_explain(explain: str) -> str:
    explain = (explain or "").strip()
    if len(explain) <= MAX_EXPLAIN_LENGTH:
        return explain
    return explain[:MAX_EXPLAIN_LENGTH]
