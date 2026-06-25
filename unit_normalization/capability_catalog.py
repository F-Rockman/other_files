"""Build field catalogs from question recommendation capability cards."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from question_recommendation.capability_loader import load_capability_cards
from question_recommendation.capability_matching import (
    matching_domain_cards,
    subcomponents_matching_text,
)
from question_recommendation.models import DeviceCapabilityProfile, RecommendationContext

from .models import CatalogField


def build_catalog_fields(
    text: str,
    *,
    context: Any = None,
    domain_cards: Sequence[DeviceCapabilityProfile] = (),
    logical_model_dir: Optional[str] = None,
) -> list[CatalogField]:
    """Build supported metric/property fields for the current query context."""
    cards = list(domain_cards)
    if not cards:
        cards, _ = load_capability_cards(logical_model_dir)
    resolved_context = _resolve_context(text, context)
    scoped_cards = matching_domain_cards(resolved_context, cards)
    if not scoped_cards:
        scoped_cards = cards

    scoped_subcomponents = subcomponents_matching_text(text, scoped_cards)
    result: list[CatalogField] = []
    for card in scoped_cards:
        result.extend(_card_fields(card))
        for spec in card.subcomponents:
            if scoped_subcomponents and (card, spec) not in scoped_subcomponents:
                continue
            result.extend(
                CatalogField(
                    name=name,
                    field_type="metric",
                    device_types=card.device_types,
                    subcomponent_types=spec.types,
                )
                for name in spec.metrics
            )
            result.extend(
                CatalogField(
                    name=name,
                    field_type="property",
                    device_types=card.device_types,
                    subcomponent_types=spec.types,
                )
                for name in spec.properties
            )
    return _dedupe_fields(result)


def _resolve_context(text: str, context: Any) -> RecommendationContext:
    if isinstance(context, RecommendationContext):
        if context.question:
            return context
        data = context.to_dict()
        data["question"] = text
        return RecommendationContext.from_dict(data)
    if isinstance(context, dict):
        data = dict(context)
        data.setdefault("question", text)
        return RecommendationContext.from_dict(data)
    return RecommendationContext(question=text)


def _card_fields(card: DeviceCapabilityProfile) -> list[CatalogField]:
    result: list[CatalogField] = []
    result.extend(
        CatalogField(
            name=name,
            field_type="metric",
            device_types=card.device_types,
        )
        for name in card.metrics
    )
    result.extend(
        CatalogField(
            name=name,
            field_type="property",
            device_types=card.device_types,
        )
        for name in card.properties
    )
    return result


def _dedupe_fields(fields: Sequence[CatalogField]) -> list[CatalogField]:
    result: list[CatalogField] = []
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    for field in fields:
        key = (
            field.name.casefold(),
            field.field_type,
            tuple(item.casefold() for item in field.device_types),
            tuple(item.casefold() for item in field.subcomponent_types),
        )
        if field.name and key not in seen:
            seen.add(key)
            result.append(field)
    return result
