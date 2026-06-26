"""最小化上下文 + 六类能力召回 + LLM 表达的问数推荐调用器。"""

import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .capabilities import recommend_capabilities
from .capability_loader import load_capability_cards
from .config import EXPLAIN_FIELD, LLM_CHAT_CALL_ERROR_REASON, RECOMMENDS_FIELD
from .field_analysis import analyze_candidate_fields
from .logical_model_reader import load_metadata_tables
from .models import DeviceCapabilityProfile, MetadataTable, RecommendationContext
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
    Chat LLM 自然化表达。LLM 返回结构合法时直接返回，不做内容过滤或补足。
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
    messages = _build_chat_messages(
        normalized_context,
        metadata_tables,
        [item.to_dict() for item in candidate_capabilities],
        domain_cards,
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
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
) -> List[Dict[str, str]]:
    """将标准上下文、按表元数据和候选能力组装为 Chat API messages。"""
    field_analysis = analyze_candidate_fields(
        context, candidate_capabilities, metadata_tables, domain_cards
    )
    simplify_analysis = analyze_simplify_constraints(context)
    user_prompt = QUESTION_RECOMMENDATION_USER_TEMPLATE.format(
        recommendation_context_json=_json_dumps(context.to_dict()),
        candidate_capabilities_json=_json_dumps(candidate_capabilities),
        metadata_tables_json=_json_dumps([table.to_dict() for table in metadata_tables]),
        candidate_field_analysis_json=_json_dumps(field_analysis),
        simplify_analysis_json=_json_dumps(simplify_analysis),
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


def _json_dumps(value: Any) -> str:
    """将 Prompt 输入序列化为缩进 JSON，并保留中文字符。"""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _load_prompt_document() -> Mapping[str, Any]:
    """读取同目录 YAML Prompt 配置。"""
    import yaml

    config_path = Path(__file__).with_name("prompt.yaml")
    with config_path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file)
    if not isinstance(document, Mapping):
        raise ValueError("question_recommendation/prompt.yaml must contain a mapping")
    return document


def _prompt_text(name: str) -> str:
    """读取标准 Prompt 块的文本内容。"""
    block = _PROMPT_DOCUMENT.get(name)
    return _block_prompt(block, name)


def _prompt_mapping(name: str) -> Mapping[str, Any]:
    """读取嵌套 Prompt 块映射。"""
    value = _PROMPT_DOCUMENT.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"Prompt section {name!r} must contain a mapping")
    return value


def _block_prompt(block: Any, name: str) -> str:
    """校验并提取 YAML Prompt 块文本。"""
    if not isinstance(block, Mapping):
        raise ValueError(f"Prompt block {name!r} must be a mapping")
    prompt = block.get("prompt")
    description = block.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"Prompt block {name!r} requires a non-empty description")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Prompt block {name!r} requires a non-empty prompt")
    return prompt.rstrip("\n")


def _build_system_prompt(context: Any, metadata_tables: Sequence[Any] = ()) -> str:
    """按结构化上下文精确选择场景片段，生成运行时 system Prompt。"""
    fragments = [_CORE_RULES]
    _append_recovery_fragment(fragments, context)
    if _needs_recovery_direction(context):
        fragments.append(_RECOVERY_DIRECTION_RULES)
    if _context_value(context, "subnet"):
        fragments.append(_SUBNET_RULES)
    if _has_usable_metadata(metadata_tables):
        fragments.append(_METADATA_RULES)
    else:
        fragments.append(_NO_METADATA_RULES)
    fragments.append(_OUTPUT_RULES)
    return _join_prompt_fragments(fragments)


def _append_recovery_fragment(fragments: list[str], context: Any) -> None:
    """按稳定优先级向 Prompt 添加唯一恢复策略片段。"""
    strategy = str(_context_value(context, "recovery_strategy") or "").strip()
    intention = str(_context_value(context, "intention") or "").strip()
    if strategy == "simplify":
        fragments.append(_SIMPLIFY_RULES)
    elif not intention:
        fragments.append(_EMPTY_INTENTION_BASIC_RULES)
    elif strategy == "basic":
        fragments.append(_BASIC_RULES)
    elif strategy in _RECOVERY_RULES:
        fragments.append(_RECOVERY_RULES[strategy])
    elif not strategy:
        fragments.append(_NORMAL_RULES)


def _needs_recovery_direction(context: Any) -> bool:
    """判断拒答场景是否缺少结构化设备和子部件方向。"""
    strategy = str(_context_value(context, "recovery_strategy") or "").strip()
    if not strategy:
        return False
    if _nonempty_device_types(context):
        return False
    return not bool(_context_value(context, "subcomponent_types"))


def _nonempty_device_types(context: Any) -> list[str]:
    """从上下文设备条件中提取非空设备类型。"""
    result: list[str] = []
    devices = _context_value(context, "devices") or []
    for device in devices:
        device_type = _item_value(device, "device_type")
        if device_type:
            result.append(str(device_type).strip())
    return result


def _has_usable_metadata(metadata_tables: Sequence[Any]) -> bool:
    """判断按表元数据中是否至少存在一个非空字段业务描述。"""
    for table in metadata_tables or ():
        columns = _item_value(table, "columns") or []
        for column in columns:
            if str(_item_value(column, "column_description") or "").strip():
                return True
    return False


def _context_value(context: Any, name: str) -> Any:
    """读取上下文对象或字典中的字段。"""
    if isinstance(context, Mapping):
        return context.get(name)
    return getattr(context, name, None)


def _item_value(item: Any, name: str) -> Any:
    """读取上下文子项或元数据子项中的字段。"""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _dedupe_fragments(fragments: Sequence[str]) -> list[str]:
    """按首次出现顺序去除重复 Prompt 片段。"""
    result: list[str] = []
    for fragment in fragments:
        if fragment not in result:
            result.append(fragment)
    return result


def _join_prompt_fragments(fragments: Sequence[str]) -> str:
    """按统一分隔符组装 Prompt 片段。"""
    return "\n\n".join(_dedupe_fragments(fragments))


_PROMPT_DOCUMENT = _load_prompt_document()

_CORE_RULES = _prompt_text("core_rules")
_OUTPUT_RULES = _prompt_text("output_rules")
_NORMAL_RULES = _prompt_text("normal_rules")
_SIMPLIFY_RULES = _prompt_text("simplify_rules")
_EMPTY_INTENTION_BASIC_RULES = _prompt_text("empty_intention_basic_rules")
_BASIC_RULES = _prompt_text("basic_rules")
_RECOVERY_RULES = {
    name: _block_prompt(block, f"recovery_rules.{name}")
    for name, block in _prompt_mapping("recovery_rules").items()
}
_RECOVERY_DIRECTION_RULES = _prompt_text("recovery_direction_rules")
_SUBNET_RULES = _prompt_text("subnet_rules")
_METADATA_RULES = _prompt_text("metadata_rules")
_NO_METADATA_RULES = _prompt_text("no_metadata_rules")

QUESTION_RECOMMENDATION_SYSTEM_PROMPT = _join_prompt_fragments([_CORE_RULES, _OUTPUT_RULES])
QUESTION_RECOMMENDATION_USER_TEMPLATE = _prompt_text("user_template")

# 兼容既有常量导入；运行时 Chat 接口会在核心规则与输出规则之间插入场景片段。
QUESTION_RECOMMENDATION_PROMPT = _join_prompt_fragments(
    [QUESTION_RECOMMENDATION_SYSTEM_PROMPT, QUESTION_RECOMMENDATION_USER_TEMPLATE]
)
