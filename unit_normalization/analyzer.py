"""Deterministic unit and metric correction analyzer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

from question_recommendation.models import DeviceCapabilityProfile

from .capability_catalog import build_catalog_fields
from .config import load_unit_config
from .models import (
    CORRECTION_METRIC,
    CORRECTION_UNIT,
    CatalogField,
    CorrectionCandidate,
    MatchedField,
    MatchedUnit,
    MetricCorrectionRule,
    MetricFamily,
    STATUS_AMBIGUOUS,
    STATUS_CORRECTED,
    STATUS_MATCHED,
    STATUS_NO_UNIT,
    STATUS_UNKNOWN,
    STATUS_UNSAFE,
    Span,
    UnitConfig,
    UnitCorrectionResult,
)
from .renderer import render_business_knowledge

AMBIGUITY_MARGIN = 0.05


def build_unit_correction_knowledge(
    text: str,
    *,
    context: Any = None,
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    logical_model_dir: Optional[str] = None,
    config_path: Optional[Union[str, Path]] = None,
) -> str:
    """Return prompt-ready unit correction business knowledge."""
    return analyze_unit_correction(
        text,
        context=context,
        domain_cards=domain_cards,
        logical_model_dir=logical_model_dir,
        config_path=config_path,
    ).business_knowledge


def analyze_unit_correction(
    text: str,
    *,
    context: Any = None,
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    logical_model_dir: Optional[str] = None,
    config_path: Optional[Union[str, Path]] = None,
) -> UnitCorrectionResult:
    """Analyze units in a user query and return deterministic correction advice."""
    config = load_unit_config(config_path)
    original_text = str(text or "")
    matched_units = _match_units(original_text, config)
    if not matched_units:
        return UnitCorrectionResult(status=STATUS_NO_UNIT, original_text=original_text)

    catalog_fields = build_catalog_fields(
        original_text,
        context=context,
        domain_cards=domain_cards,
        logical_model_dir=logical_model_dir,
    )
    matched_fields = _match_fields(original_text, catalog_fields, config)
    if not matched_fields:
        result = UnitCorrectionResult(
            status=STATUS_UNKNOWN,
            original_text=original_text,
            matched_units=matched_units,
            matched_fields=[],
        )
        return _with_business_knowledge(result)

    candidates = _build_candidates(original_text, matched_units, matched_fields, catalog_fields, config)
    status = _classify_result(matched_units, matched_fields, candidates)
    selected = _select_candidate(candidates) if status == STATUS_CORRECTED else None
    result = UnitCorrectionResult(
        status=status,
        original_text=original_text,
        matched_units=matched_units,
        matched_fields=matched_fields,
        candidates=candidates,
        selected_correction=selected.to_correction() if selected else None,
    )
    return _with_business_knowledge(result)


def _with_business_knowledge(result: UnitCorrectionResult) -> UnitCorrectionResult:
    return UnitCorrectionResult(
        status=result.status,
        original_text=result.original_text,
        matched_units=result.matched_units,
        matched_fields=result.matched_fields,
        candidates=result.candidates,
        selected_correction=result.selected_correction,
        business_knowledge=render_business_knowledge(result),
    )


def _match_units(text: str, config: UnitConfig) -> list[MatchedUnit]:
    aliases: list[tuple[str, str, str]] = []
    for unit in config.units:
        for alias in unit.aliases:
            aliases.append((alias, unit.canonical_unit, unit.unit_type))
    aliases.sort(key=lambda item: len(item[0]), reverse=True)

    matches: list[MatchedUnit] = []
    occupied: list[range] = []
    for alias, canonical_unit, unit_type in aliases:
        for start in _find_alias_positions(text, alias):
            end = start + len(alias)
            span_range = range(start, end)
            if any(_ranges_overlap(span_range, item) for item in occupied):
                continue
            matches.append(
                MatchedUnit(
                    raw=text[start:end],
                    canonical_unit=canonical_unit,
                    unit_type=unit_type,
                    span=Span(start=start, end=end),
                )
            )
            occupied.append(span_range)
    matches.sort(key=lambda item: item.span.start)
    return matches


def _find_alias_positions(text: str, alias: str) -> Iterable[int]:
    if not alias:
        return []
    positions: list[int] = []
    start = text.find(alias)
    while start >= 0:
        end = start + len(alias)
        if _unit_boundary_matches(text, start, end):
            positions.append(start)
        start = text.find(alias, start + 1)
    return positions


def _unit_boundary_matches(text: str, start: int, end: int) -> bool:
    prev_char = text[start - 1] if start > 0 else ""
    next_char = text[end] if end < len(text) else ""
    return not _is_ascii_token(prev_char) and not _is_ascii_token(next_char)


def _is_ascii_token(char: str) -> bool:
    return bool(char) and char.isascii() and (char.isalnum() or char in {"_", "."})


def _ranges_overlap(left: range, right: range) -> bool:
    return left.start < right.stop and right.start < left.stop


def _match_fields(
    text: str,
    catalog_fields: Sequence[CatalogField],
    config: UnitConfig,
) -> list[MatchedField]:
    terms: list[MatchedField] = []
    for field in catalog_fields:
        family = _family_for_term(field.name, config.metric_families)
        allowed = family.allowed_unit_types if family else _infer_allowed_unit_types(field.name, config)
        if not allowed:
            continue
        for start in _find_text_positions(text, field.name):
            terms.append(
                MatchedField(
                    raw=text[start:start + len(field.name)],
                    canonical_field=field.name,
                    field_type=field.field_type,
                    allowed_unit_types=allowed,
                    span=Span(start=start, end=start + len(field.name)),
                    device_types=field.device_types,
                    subcomponent_types=field.subcomponent_types,
                    generic=bool(family and family.generic),
                )
            )
    for family in config.metric_families:
        for term in family.terms:
            if _catalog_contains(catalog_fields, term):
                continue
            for start in _find_text_positions(text, term):
                terms.append(
                    MatchedField(
                        raw=text[start:start + len(term)],
                        canonical_field=family.canonical_metric or term,
                        field_type="metric",
                        allowed_unit_types=family.allowed_unit_types,
                        span=Span(start=start, end=start + len(term)),
                        generic=family.generic,
                    )
                )
    return _dedupe_matched_fields(terms)


def _find_text_positions(text: str, term: str) -> Iterable[int]:
    if not term:
        return []
    normalized_text = text.casefold()
    normalized_term = term.casefold()
    positions: list[int] = []
    start = normalized_text.find(normalized_term)
    while start >= 0:
        positions.append(start)
        start = normalized_text.find(normalized_term, start + 1)
    return positions


def _dedupe_matched_fields(fields: Sequence[MatchedField]) -> list[MatchedField]:
    result: list[MatchedField] = []
    seen: set[tuple[str, int, int]] = set()
    for field in sorted(fields, key=lambda item: (item.span.start, -(item.span.end - item.span.start))):
        key = (field.canonical_field.casefold(), field.span.start, field.span.end)
        if key in seen:
            continue
        if any(
            field.span.start >= other.span.start
            and field.span.end <= other.span.end
            and (other.span.end - other.span.start) > (field.span.end - field.span.start)
            for other in result
        ):
            continue
        seen.add(key)
        result.append(field)
    return result


def _catalog_contains(catalog_fields: Sequence[CatalogField], term: str) -> bool:
    normalized = term.casefold()
    return any(field.name.casefold() == normalized for field in catalog_fields)


def _family_for_term(term: str, families: Sequence[MetricFamily]) -> Optional[MetricFamily]:
    normalized = term.casefold()
    for family in families:
        if normalized == (family.canonical_metric or "").casefold():
            return family
        if normalized in {item.casefold() for item in family.terms}:
            return family
    return None


def _infer_allowed_unit_types(term: str, config: UnitConfig) -> list[str]:
    family = _family_for_term(term, config.metric_families)
    return family.allowed_unit_types if family else []


def _build_candidates(
    text: str,
    units: Sequence[MatchedUnit],
    fields: Sequence[MatchedField],
    catalog_fields: Sequence[CatalogField],
    config: UnitConfig,
) -> list[CorrectionCandidate]:
    candidates: list[CorrectionCandidate] = []
    for field in fields:
        unit = _nearest_unit(field, units)
        if unit is None:
            continue
        if unit.unit_type in field.allowed_unit_types and not field.generic:
            continue
        candidates.extend(_unit_candidates(field, unit, config))
        candidates.extend(_metric_candidates(text, field, unit, catalog_fields, config))
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def _nearest_unit(field: MatchedField, units: Sequence[MatchedUnit]) -> Optional[MatchedUnit]:
    if not units:
        return None
    return min(
        units,
        key=lambda item: min(
            abs(item.span.start - field.span.end),
            abs(field.span.start - item.span.end),
        ),
    )


def _unit_candidates(
    field: MatchedField,
    unit: MatchedUnit,
    config: UnitConfig,
) -> list[CorrectionCandidate]:
    result: list[CorrectionCandidate] = []
    for rule in config.unit_correction_rules:
        if unit.raw not in rule.raw_units and unit.canonical_unit not in rule.raw_units:
            continue
        if not _field_matches_terms(field, rule.metric_terms):
            continue
        if not set(rule.expected_unit_types).intersection(field.allowed_unit_types):
            continue
        score = _confidence_score(rule.confidence) + 0.08
        reason = (
            f"指标“{field.raw}”期望单位类型为{','.join(field.allowed_unit_types)}；"
            f"原单位“{unit.raw}”可按规则纠正为“{rule.rewrite_unit_to}”"
        )
        if rule.note:
            reason = f"{reason}；{rule.note}"
        result.append(
            CorrectionCandidate(
                type=CORRECTION_UNIT,
                source=unit.raw,
                target=rule.rewrite_unit_to,
                score=min(score, 0.99),
                confidence=rule.confidence,
                reason=reason,
                span=unit.span,
            )
        )
    return result


def _metric_candidates(
    text: str,
    field: MatchedField,
    unit: MatchedUnit,
    catalog_fields: Sequence[CatalogField],
    config: UnitConfig,
) -> list[CorrectionCandidate]:
    result: list[CorrectionCandidate] = []
    for rule in config.metric_correction_rules:
        if unit.unit_type not in rule.unit_types:
            continue
        if not _field_matches_terms(field, rule.metric_terms):
            continue
        object_hit = not rule.object_terms or _contains_any(text, rule.object_terms + field.subcomponent_types)
        direction_hit = not rule.direction_terms or _contains_any(text, rule.direction_terms)
        if not object_hit or not direction_hit:
            continue
        for target in rule.rewrite_metric_to:
            if not rule.virtual_metric and not _catalog_contains(catalog_fields, target):
                continue
            score = _confidence_score(rule.confidence)
            if field.generic:
                score += 0.05
            if rule.object_terms:
                score += 0.04
            if rule.direction_terms:
                score += 0.07
            score += _target_context_bonus(target, text)
            reason = (
                f"单位“{unit.raw}”属于{unit.unit_type}；"
                f"指标词“{field.raw}”可按能力卡支持范围改写为“{target}”"
            )
            if rule.note:
                reason = f"{reason}；{rule.note}"
            result.append(
                CorrectionCandidate(
                    type=CORRECTION_METRIC,
                    source=field.raw,
                    target=target,
                    score=min(score, 0.98),
                    confidence=rule.confidence,
                    reason=reason,
                    span=field.span,
                )
            )
    return result


def _field_matches_terms(field: MatchedField, terms: Sequence[str]) -> bool:
    normalized_values = {field.raw.casefold(), field.canonical_field.casefold()}
    return any(term.casefold() in normalized_values for term in terms)


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    normalized_text = text.casefold()
    return any(term and term.casefold() in normalized_text for term in terms)


def _target_context_bonus(target: str, text: str) -> float:
    normalized_text = text.casefold()
    normalized_target = target.casefold()
    bonus = 0.0
    if normalized_target in normalized_text:
        bonus += 0.04
    if any(term in normalized_text for term in ["入", "接收"]) and any(
        term in normalized_target for term in ["入", "接收"]
    ):
        bonus += 0.05
    if any(term in normalized_text for term in ["出", "发送"]) and any(
        term in normalized_target for term in ["出", "发送"]
    ):
        bonus += 0.05
    return bonus


def _confidence_score(confidence: str) -> float:
    normalized = str(confidence or "").strip().lower()
    if normalized == "high":
        return 0.86
    if normalized == "medium":
        return 0.72
    return 0.58


def _classify_result(
    units: Sequence[MatchedUnit],
    fields: Sequence[MatchedField],
    candidates: Sequence[CorrectionCandidate],
) -> str:
    if not units:
        return STATUS_NO_UNIT
    if _has_safe_match(units, fields):
        return STATUS_MATCHED
    if not candidates:
        return STATUS_UNKNOWN
    if _top_candidate_is_ambiguous(candidates):
        return STATUS_AMBIGUOUS
    top = candidates[0]
    if top.confidence != "high" and top.score < 0.78:
        return STATUS_UNSAFE
    return STATUS_CORRECTED


def _has_safe_match(units: Sequence[MatchedUnit], fields: Sequence[MatchedField]) -> bool:
    for field in fields:
        if field.generic:
            continue
        unit = _nearest_unit(field, units)
        if unit and unit.unit_type in field.allowed_unit_types:
            return True
    return False


def _top_candidate_is_ambiguous(candidates: Sequence[CorrectionCandidate]) -> bool:
    if len(candidates) < 2:
        return False
    return candidates[0].score - candidates[1].score < AMBIGUITY_MARGIN


def _select_candidate(candidates: Sequence[CorrectionCandidate]) -> Optional[CorrectionCandidate]:
    return candidates[0] if candidates else None
