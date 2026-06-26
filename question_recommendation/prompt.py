"""六类能力候选 + 动态场景规则 + LLM 自然表达的问数推荐 Prompt。"""

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def _dump_json(value: Any) -> str:
    """序列化任意对象为不含反斜杠转义的 JSON 字符串。"""
    if hasattr(value, "to_dict"):
        dumped = value.to_dict()
    elif isinstance(value, Mapping):
        dumped = dict(value)
    elif hasattr(value, "__dict__"):
        dumped = (
            {key: getattr(value, key) for key in asdict(value)}
            if hasattr(value, "__dataclass_fields__")
            else vars(value)
        )
    else:
        dumped = value
    text = json.dumps(dumped, ensure_ascii=False, indent=2)
    return text.replace("\\/", "/").replace("\\u0027", "'")


def format_recommendation_prompt(
    context: Any,
    candidate_capabilities: Any,
    metadata_tables: Any = None,
    candidate_templates: Any = None,
) -> str:
    """使用兼容常量组装不含运行时场景片段的完整推荐 Prompt。"""
    return QUESTION_RECOMMENDATION_PROMPT.format(
        recommendation_context_json=_dump_json(context),
        candidate_capabilities_json=_dump_json(candidate_capabilities),
        metadata_tables_json=_dump_json(metadata_tables) if metadata_tables else "null",
        candidate_templates_json=_dump_json(candidate_templates) if candidate_templates else "null",
    )


def _load_prompt_document() -> Mapping[str, Any]:
    """读取同目录 YAML Prompt 配置。"""
    config_path = Path(__file__).with_name("prompt.yaml")
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError:
        document = _load_simple_prompt_yaml(text)
    else:
        document = yaml.safe_load(text)
    if not isinstance(document, Mapping):
        raise ValueError("question_recommendation/prompt.yaml must contain a mapping")
    return document


def _load_simple_prompt_yaml(text: str) -> Mapping[str, Any]:
    """在 PyYAML 不可用时解析本模块受控的 Prompt YAML 子集。"""
    lines = text.splitlines()
    document: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if not line.endswith(":") or _indent(line) != 0:
            raise ValueError("Unsupported prompt.yaml structure")
        key = line[:-1]
        index += 1
        if key == "recovery_rules":
            section, index = _parse_nested_prompt_blocks(lines, index)
            document[key] = section
        else:
            block, index = _parse_prompt_block(lines, index, 2)
            document[key] = block
    return document


def _parse_nested_prompt_blocks(lines: Sequence[str], index: int) -> tuple[dict[str, Any], int]:
    """解析 recovery_rules 这类二级 Prompt 映射。"""
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        indent = _indent(line)
        if indent == 0:
            break
        if indent != 2 or not line.strip().endswith(":"):
            raise ValueError("Unsupported nested prompt.yaml structure")
        key = line.strip()[:-1]
        block, index = _parse_prompt_block(lines, index + 1, 4)
        result[key] = block
    return result, index


def _parse_prompt_block(
    lines: Sequence[str],
    index: int,
    indent: int,
) -> tuple[dict[str, str], int]:
    """解析包含 description 和 prompt 的标准 Prompt 块。"""
    block: dict[str, str] = {}
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError("Unsupported prompt block indentation")
        content = line[indent:]
        if content.startswith("description:"):
            block["description"] = content.split(":", 1)[1].strip()
            index += 1
        elif content == "prompt: |":
            prompt, index = _parse_prompt_scalar(lines, index + 1, indent + 2)
            block["prompt"] = prompt
        else:
            raise ValueError("Unsupported prompt block field")
    return block, index


def _parse_prompt_scalar(
    lines: Sequence[str],
    index: int,
    indent: int,
) -> tuple[str, int]:
    """解析 YAML block scalar 文本。"""
    result: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip() and _indent(line) < indent:
            break
        result.append(line[indent:] if len(line) >= indent else "")
        index += 1
    return "\n".join(result).rstrip("\n"), index


def _indent(line: str) -> int:
    """返回行首空格数。"""
    return len(line) - len(line.lstrip(" "))


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
    return "\n\n".join(_dedupe_fragments(fragments))


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


QUESTION_RECOMMENDATION_SYSTEM_PROMPT = _CORE_RULES + "\n\n" + _OUTPUT_RULES
QUESTION_RECOMMENDATION_USER_TEMPLATE = _prompt_text("user_template")

# 兼容既有常量导入；运行时 Chat 接口会在核心规则与输出规则之间插入场景片段。
QUESTION_RECOMMENDATION_PROMPT = (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT + "\n\n" + QUESTION_RECOMMENDATION_USER_TEMPLATE
)
