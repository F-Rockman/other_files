"""Render unit correction results for prompt business knowledge."""

from __future__ import annotations

from .models import (
    CORRECTION_METRIC,
    CORRECTION_UNIT,
    STATUS_AMBIGUOUS,
    STATUS_CORRECTED,
    STATUS_MATCHED,
    STATUS_UNSAFE,
    UnitCorrectionResult,
)


def render_business_knowledge(result: UnitCorrectionResult) -> str:
    """Render deterministic correction output into prompt-friendly text."""
    if result.status == STATUS_CORRECTED and result.selected_correction:
        correction = result.selected_correction
        if correction.type == CORRECTION_UNIT:
            return (
                f"单位纠错结论：用户问题中的单位“{correction.source}”与指标或属性不匹配；"
                f"命中确定性单位纠错规则，应将单位改为“{correction.target}”。"
                f"标准问句中执行 rewrite_unit_to=\"{correction.target}\"，"
                f"不得改变原指标、对象范围、过滤条件或展示要求。原因：{correction.reason}"
            )
        if correction.type == CORRECTION_METRIC:
            return (
                f"单位纠错结论：用户问题中的指标或属性“{correction.source}”与单位语义不匹配；"
                f"命中确定性指标纠错规则，应将指标改为“{correction.target}”。"
                f"标准问句中执行 rewrite_metric_to=\"{correction.target}\"，"
                f"不得补充用户未表达的对象、条件、时间或展示要求。原因：{correction.reason}"
            )
    if result.status == STATUS_MATCHED and result.matched_units and result.matched_fields:
        unit = result.matched_units[0]
        field = result.matched_fields[0]
        return (
            f"单位校验结论：用户问题中的“{field.raw} {unit.raw}”单位类型匹配，"
            "不得基于单位自行改写指标或单位。"
        )
    if result.status == STATUS_UNSAFE:
        return _render_guardrail(result, "单位安全结论")
    if result.status == STATUS_AMBIGUOUS:
        return _render_guardrail(result, "单位歧义结论")
    return ""


def _render_guardrail(result: UnitCorrectionResult, prefix: str) -> str:
    if result.candidates:
        choices = "、".join(
            f"{item.source}->{item.target}(score={item.score:.2f})"
            for item in result.candidates[:3]
        )
        return (
            f"{prefix}：存在单位与指标不匹配，但无法稳定选择唯一纠错结果；"
            f"候选为：{choices}。标准问句必须保留用户原始单位和指标，不得自行纠错。"
        )
    if result.matched_units:
        units = "、".join(item.raw for item in result.matched_units)
        return f"{prefix}：单位“{units}”未获得足够证据自动纠错，标准问句必须保留原单位。"
    return ""
