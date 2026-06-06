"""最小化上下文 + 内置能力卡 + LLM 表达的问数推荐模块。"""

from .capabilities import RankedCapability, load_capability_cards, recommend_capabilities
from .context_builder import build_recommendation_context
from .metadata_loader import LogicalMetadataError, load_logical_metadata
from .models import (
    AlarmCondition,
    CapabilityCard,
    Identifier,
    MetadataColumn,
    RecommendationContext,
)
from .prompt import (
    QUESTION_RECOMMENDATION_PROMPT,
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
)
from .recommender import QuestionRecommendationError, recommend_questions_chat

__all__ = [
    "QUESTION_RECOMMENDATION_PROMPT",
    "QUESTION_RECOMMENDATION_SYSTEM_PROMPT",
    "QUESTION_RECOMMENDATION_USER_TEMPLATE",
    "Identifier",
    "AlarmCondition",
    "RecommendationContext",
    "CapabilityCard",
    "MetadataColumn",
    "RankedCapability",
    "build_recommendation_context",
    "load_capability_cards",
    "recommend_capabilities",
    "LogicalMetadataError",
    "load_logical_metadata",
    "QuestionRecommendationError",
    "recommend_questions_chat",
]
