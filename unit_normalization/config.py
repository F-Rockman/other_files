"""Configuration loading for unit normalization."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .models import (
    MetricCorrectionRule,
    MetricFamily,
    UnitConfig,
    UnitCorrectionRule,
    UnitDefinition,
    UnsafeConfusion,
)


def load_unit_config(config_path: Optional[Union[str, Path]] = None) -> UnitConfig:
    """Load unit normalization rules from JSON."""
    payload = _load_payload(config_path)
    return UnitConfig(
        units=[
            UnitDefinition(
                canonical_unit=str(item.get("canonical_unit", "")).strip(),
                aliases=_as_list(item.get("aliases")),
                unit_type=str(item.get("unit_type", "")).strip(),
            )
            for item in payload.get("units", [])
            if isinstance(item, Mapping)
        ],
        unsafe_confusions=[
            UnsafeConfusion(
                raw_unit=str(item.get("raw_unit", "")).strip(),
                confused_with=str(item.get("confused_with", "")).strip(),
                allow_expected_unit_types=_as_list(item.get("allow_expected_unit_types")),
                note=str(item.get("note", "")).strip(),
            )
            for item in payload.get("unsafe_confusions", [])
            if isinstance(item, Mapping)
        ],
        metric_families=[
            MetricFamily(
                terms=_as_list(item.get("terms")),
                canonical_metric=str(item.get("canonical_metric", "")).strip(),
                allowed_unit_types=_as_list(item.get("allowed_unit_types")),
                preferred_unit=str(item.get("preferred_unit", "")).strip(),
                generic=bool(item.get("generic", False)),
            )
            for item in payload.get("metric_families", [])
            if isinstance(item, Mapping)
        ],
        unit_correction_rules=[
            UnitCorrectionRule(
                metric_terms=_as_list(item.get("metric_terms")),
                raw_units=_as_list(item.get("raw_units")),
                rewrite_unit_to=str(item.get("rewrite_unit_to", "")).strip(),
                expected_unit_types=_as_list(item.get("expected_unit_types")),
                confidence=str(item.get("confidence", "medium") or "medium"),
                note=str(item.get("note", "")).strip(),
            )
            for item in payload.get("unit_correction_rules", [])
            if isinstance(item, Mapping)
        ],
        metric_correction_rules=[
            MetricCorrectionRule(
                metric_terms=_as_list(item.get("metric_terms")),
                unit_types=_as_list(item.get("unit_types")),
                rewrite_metric_to=_as_list(item.get("rewrite_metric_to")),
                object_terms=_as_list(item.get("object_terms")),
                direction_terms=_as_list(item.get("direction_terms")),
                confidence=str(item.get("confidence", "medium") or "medium"),
                virtual_metric=bool(item.get("virtual_metric", False)),
                note=str(item.get("note", "")).strip(),
            )
            for item in payload.get("metric_correction_rules", [])
            if isinstance(item, Mapping)
        ],
    )


def _load_payload(config_path: Optional[Union[str, Path]]) -> dict[str, Any]:
    if config_path:
        raw_text = Path(config_path).read_text(encoding="utf-8")
    else:
        path = resources.files("unit_normalization").joinpath("data/unit_rules.json")
        raw_text = path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    return payload if isinstance(payload, dict) else {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result
