"""
黑化改写模块

核心功能：
- normalize: 黑化改写主函数（Completion API）
- normalize_chat: 黑化改写主函数（Chat API 版本）
- 三层管线：L1 最长匹配 AC 自动机 → L2 jieba 边界校验 → L3 LLM 兜底

LLM 客户端为通用 Callable，不绑定任何特定 LLM SDK。
"""

import json
import re
from typing import Callable, Optional

import ahocorasick
import jieba

from .prompt import SLANG_NORMALIZER_SYSTEM_PROMPT, SLANG_NORMALIZER_USER_TEMPLATE
from .config import (
    DEFAULT_SLANG_TYPE,
    DEFAULT_COMPOUND_TYPE,
    DEFAULT_LITERAL_TYPE,
    DEFAULT_SUBSTRING_TYPE,
    LLM_OUTPUT_FORMAT_ERROR_REASON,
    LLM_CALL_ERROR_REASON,
    LLM_CHAT_CALL_ERROR_REASON,
    TEXT_FIELD,
    MATCHES_FIELD,
    UNRESOLVED_FIELD,
    TYPE_FIELD,
    CONFIDENCE_FIELD,
    REASONING_FIELD,
    ORIGINAL_FIELD,
    REPLACEMENT_FIELD,
    START_FIELD,
    END_FIELD,
    VALID_TYPES,
)


class SlangNormalizerError(Exception):
    """黑化改写异常"""
    pass


def normalize(
    text: str,
    slang_dict: dict,
    compound_dict: set,
    llm_client: Optional[Callable[[str], str]] = None,
) -> dict:
    """
    黑化改写主函数（Completion API）

    参数:
        text: 用户输入文本
        slang_dict: 黑化→规范映射字典
        compound_dict: 合法复合词集合（包含黑化子串的合法词）
        llm_client: LLM 客户端（可选，用于 L3 兜底）

    返回:
        dict: {"text": str (改写后的文本), "matches": list[dict], "unresolved": list[dict]}
    """
    if not text:
        return {TEXT_FIELD: "", MATCHES_FIELD: [], UNRESOLVED_FIELD: []}

    l1_matches = _l1_ac_longest_match(text, slang_dict, compound_dict)
    l2_results = _l2_boundary_check(text, l1_matches, compound_dict)

    accepted = l2_results["accepted"]
    unresolved = l2_results["unresolved"]

    if llm_client and unresolved:
        l3_results = _l3_llm_resolve(text, unresolved, slang_dict, llm_client)
        accepted.extend(l3_results["accepted"])
        unresolved = l3_results["unresolved"]

    normalized_text = _apply_replacements(text, accepted)

    return {
        TEXT_FIELD: normalized_text,
        MATCHES_FIELD: accepted,
        UNRESOLVED_FIELD: unresolved,
    }


def normalize_chat(
    text: str,
    slang_dict: dict,
    compound_dict: set,
    llm_chat_client: Optional[Callable[[list[dict]], str]] = None,
) -> dict:
    """
    黑化改写主函数（Chat API 版本）

    参数:
        text: 用户输入文本
        slang_dict: 黑化→规范映射字典
        compound_dict: 合法复合词集合（包含黑化子串的合法词）
        llm_chat_client: LLM Chat 客户端（可选，用于 L3 兜底）

    返回:
        dict: {"text": str (改写后的文本), "matches": list[dict], "unresolved": list[dict]}
    """
    if not text:
        return {TEXT_FIELD: "", MATCHES_FIELD: [], UNRESOLVED_FIELD: []}

    l1_matches = _l1_ac_longest_match(text, slang_dict, compound_dict)
    l2_results = _l2_boundary_check(text, l1_matches, compound_dict)

    accepted = l2_results["accepted"]
    unresolved = l2_results["unresolved"]

    if llm_chat_client and unresolved:
        l3_results = _l3_llm_resolve_chat(text, unresolved, slang_dict, llm_chat_client)
        accepted.extend(l3_results["accepted"])
        unresolved = l3_results["unresolved"]

    normalized_text = _apply_replacements(text, accepted)

    return {
        TEXT_FIELD: normalized_text,
        MATCHES_FIELD: accepted,
        UNRESOLVED_FIELD: unresolved,
    }


def _l1_ac_longest_match(text: str, slang_dict: dict, compound_dict: set) -> list[dict]:
    """
    L1: 最长匹配 AC 自动机

    同时注册黑化词和合法复合词，使用 iter_long() 获取最长匹配。
    compound 类型匹配直接跳过（不替换），slang 类型匹配进入 L2 校验。
    """
    A = ahocorasick.Automaton()

    idx = 0
    for slang, standard in slang_dict.items():
        A.add_word(slang, (idx, slang, DEFAULT_SLANG_TYPE, standard))
        idx += 1

    for compound in compound_dict:
        A.add_word(compound, (idx, compound, DEFAULT_COMPOUND_TYPE, None))
        idx += 1

    A.make_automaton()

    matches = []
    for end_pos, (idx, key, match_type, replacement) in A.iter_long(text):
        start_pos = end_pos - len(key) + 1
        if match_type == DEFAULT_COMPOUND_TYPE:
            continue
        matches.append({
            ORIGINAL_FIELD: key,
            REPLACEMENT_FIELD: replacement,
            START_FIELD: start_pos,
            END_FIELD: end_pos + 1,
            TYPE_FIELD: DEFAULT_SLANG_TYPE,
        })

    return matches


def _l2_boundary_check(text: str, l1_matches: list[dict], compound_dict: set) -> dict:
    """
    L2: jieba 边界校验

    对 L1 输出的 slang 匹配进行边界校验：
    - 如果 slang 匹配的起止位置恰好对应一个 jieba token 且 token 文本等于 slang 词 → 接受
    - 如果 slang 匹配是某个更长 jieba token 的子串 → 拒绝（子串假阳性）
    """
    if not l1_matches:
        return {"accepted": [], "unresolved": []}

    for compound in compound_dict:
        jieba.add_word(compound)

    tokens = list(jieba.cut(text))
    token_positions = []
    pos = 0
    for tok in tokens:
        token_positions.append((pos, pos + len(tok), tok))
        pos += len(tok)

    accepted = []
    unresolved = []

    for match in l1_matches:
        start = match[START_FIELD]
        end = match[END_FIELD]
        slang_word = match[ORIGINAL_FIELD]

        is_valid = False
        is_straddling = False

        for tok_start, tok_end, tok_text in token_positions:
            if tok_start == start and tok_end == end and tok_text == slang_word:
                is_valid = True
                break
            if tok_start <= start and tok_end >= end and tok_text != slang_word:
                is_straddling = True
                break

        if is_valid:
            accepted.append(match)
        elif is_straddling:
            match[TYPE_FIELD] = DEFAULT_SUBSTRING_TYPE
            unresolved.append(match)
        else:
            unresolved.append(match)

    return {"accepted": accepted, "unresolved": unresolved}


def _l3_llm_resolve(
    text: str,
    unresolved: list[dict],
    slang_dict: dict,
    llm_client: Callable[[str], str],
) -> dict:
    """
    L3: LLM 兜底（Completion API）

    对 L2 未解决的匹配逐个调用 LLM 判断类型。
    """
    accepted = []
    still_unresolved = []

    for match in unresolved:
        target_word = match[ORIGINAL_FIELD]
        candidates = slang_dict.get(target_word, "")

        full_prompt = SLANG_NORMALIZER_SYSTEM_PROMPT + "\n\n" + SLANG_NORMALIZER_USER_TEMPLATE.format(
            text=text,
            target_word=target_word,
            candidates=candidates,
        )

        try:
            llm_response = llm_client(full_prompt)
        except Exception as e:
            match[TYPE_FIELD] = DEFAULT_LITERAL_TYPE
            match[REASONING_FIELD] = f"{LLM_CALL_ERROR_REASON}: {e}"
            still_unresolved.append(match)
            continue

        result = _parse_llm_response(llm_response)
        result_type = result.get(TYPE_FIELD, DEFAULT_LITERAL_TYPE)
        confidence = result.get(CONFIDENCE_FIELD, 0.0)
        reasoning = result.get(REASONING_FIELD, "")

        if result_type == DEFAULT_SLANG_TYPE and result_type in VALID_TYPES:
            match[TYPE_FIELD] = DEFAULT_SLANG_TYPE
            match[CONFIDENCE_FIELD] = confidence
            match[REASONING_FIELD] = reasoning
            accepted.append(match)
        else:
            match[TYPE_FIELD] = result_type if result_type in VALID_TYPES else DEFAULT_LITERAL_TYPE
            match[CONFIDENCE_FIELD] = confidence
            match[REASONING_FIELD] = reasoning
            still_unresolved.append(match)

    return {"accepted": accepted, "unresolved": still_unresolved}


def _l3_llm_resolve_chat(
    text: str,
    unresolved: list[dict],
    slang_dict: dict,
    llm_chat_client: Callable[[list[dict]], str],
) -> dict:
    """
    L3: LLM 兜底（Chat API 版本）
    """
    accepted = []
    still_unresolved = []

    for match in unresolved:
        target_word = match[ORIGINAL_FIELD]
        candidates = slang_dict.get(target_word, "")

        messages = [
            {"role": "system", "content": SLANG_NORMALIZER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": SLANG_NORMALIZER_USER_TEMPLATE.format(
                    text=text,
                    target_word=target_word,
                    candidates=candidates,
                ),
            },
        ]

        try:
            llm_response = llm_chat_client(messages)
        except Exception as e:
            match[TYPE_FIELD] = DEFAULT_LITERAL_TYPE
            match[REASONING_FIELD] = f"{LLM_CHAT_CALL_ERROR_REASON}: {e}"
            still_unresolved.append(match)
            continue

        result = _parse_llm_response(llm_response)
        result_type = result.get(TYPE_FIELD, DEFAULT_LITERAL_TYPE)
        confidence = result.get(CONFIDENCE_FIELD, 0.0)
        reasoning = result.get(REASONING_FIELD, "")

        if result_type == DEFAULT_SLANG_TYPE and result_type in VALID_TYPES:
            match[TYPE_FIELD] = DEFAULT_SLANG_TYPE
            match[CONFIDENCE_FIELD] = confidence
            match[REASONING_FIELD] = reasoning
            accepted.append(match)
        else:
            match[TYPE_FIELD] = result_type if result_type in VALID_TYPES else DEFAULT_LITERAL_TYPE
            match[CONFIDENCE_FIELD] = confidence
            match[REASONING_FIELD] = reasoning
            still_unresolved.append(match)

    return {"accepted": accepted, "unresolved": still_unresolved}


def _apply_replacements(text: str, accepted: list[dict]) -> str:
    """
    将已接受的替换应用到原文

    按位置从后往前替换，避免位置偏移。
    """
    if not accepted:
        return text

    sorted_matches = sorted(accepted, key=lambda m: m[START_FIELD], reverse=True)
    result = text
    for match in sorted_matches:
        start = match[START_FIELD]
        end = match[END_FIELD]
        replacement = match[REPLACEMENT_FIELD]
        result = result[:start] + replacement + result[end:]

    return result


def _parse_llm_response(llm_response: str) -> dict:
    """
    解析 LLM 返回的 JSON 响应

    处理策略：
    1. 直接 JSON 解析
    2. 提取 JSON 块（LLM 可能输出 markdown 包裹的 JSON）
    3. 解析失败时返回 literal + 格式异常原因
    """
    try:
        result = json.loads(llm_response)
        return _validate_result(result)
    except json.JSONDecodeError:
        pass

    json_block = _extract_json_block(llm_response)
    if json_block:
        try:
            result = json.loads(json_block)
            return _validate_result(result)
        except json.JSONDecodeError:
            pass

    return {
        TYPE_FIELD: DEFAULT_LITERAL_TYPE,
        CONFIDENCE_FIELD: 0.0,
        REASONING_FIELD: LLM_OUTPUT_FORMAT_ERROR_REASON,
    }


def _extract_json_block(text: str) -> Optional[str]:
    """
    从 LLM 响应中提取 JSON 代码块

    支持 ```json ... ``` 和 ``` ... ``` 格式
    """
    patterns = [
        r'```json\s*\n?(.*?)\n?\s*```',
        r'```\s*\n?(.*?)\n?\s*```',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        return match.group(0)

    return None


def _validate_result(result: dict) -> dict:
    """
    验证并规范化 LLM 返回的结果

    确保 type 字段合法，confidence 和 reasoning 字段存在
    """
    result_type = result.get(TYPE_FIELD, DEFAULT_LITERAL_TYPE)
    confidence = result.get(CONFIDENCE_FIELD, 0.0)
    reasoning = result.get(REASONING_FIELD, "")

    if result_type not in VALID_TYPES:
        result_type = DEFAULT_LITERAL_TYPE
        reasoning = LLM_OUTPUT_FORMAT_ERROR_REASON

    return {
        TYPE_FIELD: result_type,
        CONFIDENCE_FIELD: confidence,
        REASONING_FIELD: reasoning,
    }