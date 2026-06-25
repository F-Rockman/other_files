"""Data models for deterministic unit normalization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


STATUS_NO_UNIT = "no_unit"
STATUS_MATCHED = "matched"
STATUS_CORRECTED = "corrected"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNSAFE = "unsafe"
STATUS_UNKNOWN = "unknown"

CORRECTION_UNIT = "unit"
CORRECTION_METRIC = "metric"
CORRECTION_NONE = "none"


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class UnitDefinition:
    canonical_unit: str
    aliases: List[str]
    unit_type: str


@dataclass(frozen=True)
class UnsafeConfusion:
    raw_unit: str
    confused_with: str
    allow_expected_unit_types: List[str]
    note: str = ""


@dataclass(frozen=True)
class MetricFamily:
    terms: List[str]
    canonical_metric: str
    allowed_unit_types: List[str]
    preferred_unit: str = ""
    generic: bool = False


@dataclass(frozen=True)
class UnitCorrectionRule:
    metric_terms: List[str]
    raw_units: List[str]
    rewrite_unit_to: str
    expected_unit_types: List[str]
    confidence: str = "medium"
    note: str = ""


@dataclass(frozen=True)
class MetricCorrectionRule:
    metric_terms: List[str]
    unit_types: List[str]
    rewrite_metric_to: List[str]
    object_terms: List[str] = field(default_factory=list)
    direction_terms: List[str] = field(default_factory=list)
    confidence: str = "medium"
    virtual_metric: bool = False
    note: str = ""


@dataclass(frozen=True)
class UnitConfig:
    units: List[UnitDefinition]
    unsafe_confusions: List[UnsafeConfusion]
    metric_families: List[MetricFamily]
    unit_correction_rules: List[UnitCorrectionRule]
    metric_correction_rules: List[MetricCorrectionRule]


@dataclass(frozen=True)
class CatalogField:
    name: str
    field_type: str
    device_types: List[str] = field(default_factory=list)
    subcomponent_types: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchedUnit:
    raw: str
    canonical_unit: str
    unit_type: str
    span: Span

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["span"] = self.span.to_dict()
        return data


@dataclass(frozen=True)
class MatchedField:
    raw: str
    canonical_field: str
    field_type: str
    allowed_unit_types: List[str]
    span: Span
    device_types: List[str] = field(default_factory=list)
    subcomponent_types: List[str] = field(default_factory=list)
    generic: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["span"] = self.span.to_dict()
        return data


@dataclass(frozen=True)
class Correction:
    type: str
    source: str
    target: str
    confidence: float
    reason: str
    span: Optional[Span] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["span"] = self.span.to_dict() if self.span else None
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


@dataclass(frozen=True)
class CorrectionCandidate:
    type: str
    source: str
    target: str
    score: float
    confidence: str
    reason: str
    span: Optional[Span] = None

    def to_correction(self) -> Correction:
        return Correction(
            type=self.type,
            source=self.source,
            target=self.target,
            confidence=round(self.score, 3),
            reason=self.reason,
            span=self.span,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["span"] = self.span.to_dict() if self.span else None
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


@dataclass(frozen=True)
class UnitCorrectionResult:
    status: str
    original_text: str
    matched_units: List[MatchedUnit] = field(default_factory=list)
    matched_fields: List[MatchedField] = field(default_factory=list)
    candidates: List[CorrectionCandidate] = field(default_factory=list)
    selected_correction: Optional[Correction] = None
    business_knowledge: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "original_text": self.original_text,
            "matched_units": [item.to_dict() for item in self.matched_units],
            "matched_fields": [item.to_dict() for item in self.matched_fields],
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_correction": (
                self.selected_correction.to_dict() if self.selected_correction else None
            ),
            "business_knowledge": self.business_knowledge,
        }
