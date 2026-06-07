"""最小化上下文 + 六类能力规格 + LLM 表达的问数推荐模块。"""

from .capabilities import (
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
    RankedCapability,
    load_device_capability_profiles,
    load_special_capabilities,
    recommend_capabilities,
    resolve_primary_capability_type,
)
from .context_builder import build_recommendation_context
from .metadata_loader import LogicalMetadataError, load_logical_metadata
from .models import (
    AlarmCondition,
    CapabilityCandidate,
    DeviceCapabilityProfile,
    Identifier,
    MetadataColumn,
    MetadataTable,
    MetricSpec,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubcomponentCapabilitySpec,
)
from .prompt import (
    QUESTION_RECOMMENDATION_PROMPT,
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
)
from .recommender import QuestionRecommendationError, recommend_questions_chat
from .refusal_rules import RefusalRecoveryRule, get_refusal_recovery_rule

__all__ = [
    "QUESTION_RECOMMENDATION_PROMPT",
    "QUESTION_RECOMMENDATION_SYSTEM_PROMPT",
    "QUESTION_RECOMMENDATION_USER_TEMPLATE",
    "Identifier",
    "AlarmCondition",
    "RecommendationContext",
    "MetricSpec",
    "SubcomponentCapabilitySpec",
    "DeviceCapabilityProfile",
    "SpecialCapabilitySpec",
    "CapabilityCandidate",
    "MetadataColumn",
    "MetadataTable",
    "RankedCapability",
    "build_recommendation_context",
    "DEVICE_INFO",
    "DEVICE_COUNT",
    "DEVICE_METRIC",
    "SUBCOMPONENT_INFO",
    "SUBCOMPONENT_COUNT",
    "SUBCOMPONENT_METRIC",
    "load_device_capability_profiles",
    "load_special_capabilities",
    "resolve_primary_capability_type",
    "recommend_capabilities",
    "LogicalMetadataError",
    "load_logical_metadata",
    "QuestionRecommendationError",
    "recommend_questions_chat",
    "RefusalRecoveryRule",
    "get_refusal_recovery_rule",
]
