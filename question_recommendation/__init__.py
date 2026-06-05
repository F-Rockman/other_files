"""
问数推荐问题生成模块。

采用"结构化模板 + LLM 表达"方案：结构化模板定义推荐能力边界，
LLM 负责排序、失败恢复和自然语言表达。
"""

from .prompt import (
    QUESTION_RECOMMENDATION_PROMPT,
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
)
from .models import MetadataColumn, RecognizedIntent, StructuredTemplate
from .recommender import QuestionRecommendationError, recommend_questions, recommend_questions_chat

__all__ = [
    "QUESTION_RECOMMENDATION_SYSTEM_PROMPT",
    "QUESTION_RECOMMENDATION_USER_TEMPLATE",
    "QUESTION_RECOMMENDATION_PROMPT",
    "RecognizedIntent",
    "StructuredTemplate",
    "MetadataColumn",
    "QuestionRecommendationError",
    "recommend_questions",
    "recommend_questions_chat",
]
