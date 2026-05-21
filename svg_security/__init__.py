"""SVG Security Validation - 防护 SVG 向量炸弹 (XML Bomb / Billion Laughs Attack)"""

from .validator import (
    SVGSecurityError,
    validate_base64_svg,
    get_safe_svg_string,
    preprocess_svg,
    create_safe_parser,
)

__all__ = [
    "SVGSecurityError",
    "validate_base64_svg",
    "get_safe_svg_string",
    "preprocess_svg",
    "create_safe_parser",
]