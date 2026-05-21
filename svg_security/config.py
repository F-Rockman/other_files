"""
SVG 安全校验配置常量

可通过修改此文件调整校验阈值和白名单。
"""

# ============ 大小限制 ============

MAX_BASE64_SIZE = 1 * 1024 * 1024       # Base64 最大 1MB
MAX_DECODED_SIZE = 700 * 1024            # 解码后最大 700KB
MAX_ATTR_VALUE_LENGTH = 1000             # 属性值最大长度

# ============ 结构限制 ============

MAX_NESTING_DEPTH = 50                   # 嵌套深度上限
MAX_ELEMENT_COUNT = 5000                 # 元素总数上限

# ============ 白名单 ============

ALLOWED_SVG_ELEMENTS = {
    'svg', 'g', 'path', 'circle', 'rect', 'ellipse', 'line',
    'polyline', 'polygon', 'text', 'tspan', 'defs',
    'linearGradient', 'radialGradient', 'stop', 'use',
    'clipPath', 'mask', 'symbol', 'marker', 'title', 'desc',
}

ALLOWED_ATTRIBUTES = {
    'id', 'class', 'd', 'cx', 'cy', 'r', 'rx', 'ry',
    'x', 'y', 'width', 'height', 'fill', 'stroke',
    'stroke-width', 'stroke-dasharray', 'transform',
    'opacity', 'fill-opacity', 'stroke-opacity',
    'viewBox', 'xmlns', 'preserveAspectRatio',
    'font-size', 'font-family', 'text-anchor',
    'x1', 'y1', 'x2', 'y2', 'points',
    'offset', 'stop-color', 'stop-opacity',
    'gradientUnits', 'gradientTransform',
    'clip-path', 'mask', 'filter',
    'style', 'color', 'display',
}

# ============ Namespace ============

SVG_NS = 'http://www.w3.org/2000/svg'

# ============ 危险属性值模式 ============

DANGEROUS_ATTRIBUTE_PATTERNS = [
    (r'javascript:', 'JavaScript URI'),
    (r'vbscript:', 'VBScript URI'),
    (r'data:text/html', 'Data URI with HTML'),
    (r'data:application', 'Data URI with executable'),
    (r'<script', 'Embedded script tag'),
    (r'eval\s*\(', 'eval() call'),
    (r'expression\s*\(', 'CSS expression()'),
    (r'-moz-binding', 'Mozilla XBL binding'),
    (r'@import', 'CSS @import'),
]

# ============ 允许的 XML 预定义实体 ============

SAFE_XML_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}