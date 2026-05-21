"""
SVG 向量炸弹 (XML Bomb) 安全校验模块

防护层级：
L1: Base64 格式/大小校验
L2: 解码后大小校验
L3: 预处理剥离危险结构（第一道防线）
L4: lxml 安全解析器（第二道防线）
L5: SVG 根元素验证
L6: 白名单校验
L7: 嵌套深度/元素数量限制
L8: 危险属性值检测

不依赖 defusedxml，使用 lxml 安全配置 + 预处理双重防线。
"""

import re
import base64
from lxml import etree

# ============ 配置常量 ============

MAX_BASE64_SIZE = 1 * 1024 * 1024       # Base64 最大 1MB
MAX_DECODED_SIZE = 700 * 1024            # 解码后最大 700KB
MAX_NESTING_DEPTH = 50                   # 嵌套深度上限
MAX_ELEMENT_COUNT = 5000                 # 元素总数上限
MAX_ATTR_VALUE_LENGTH = 1000             # 属性值最大长度

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

SVG_NS = 'http://www.w3.org/2000/svg'


class SVGSecurityError(Exception):
    """SVG 安全校验异常"""
    pass


# ============ 核心：安全 XML 解析器 ============

def create_safe_parser() -> etree.XMLParser:
    """
    创建 lxml 安全解析器，等效于 defusedxml 的防护能力

    关键配置：
    - resolve_entities=False  → 禁用实体扩展（防 XML Bomb）
    - no_network=True         → 禁止网络访问（防 SSRF）
    - load_dtd=False          → 不加载 DTD（防实体定义注入）
    - dtd_validation=False    → 不做 DTD 验证
    - huge_tree=False         → 限制树大小（防内存耗尽）
    """
    parser = etree.XMLParser(
        resolve_entities=False,      # 核心：不扩展实体，Bomb 无法触发
        no_network=True,             # 禁止加载外部资源
        load_dtd=False,              # 不加载 DTD 定义
        dtd_validation=False,        # 不验证 DTD
        huge_tree=False,             # 限制解析树大小
        remove_blank_text=True,      # 移除空白文本
        remove_comments=True,        # 移除注释（可能藏恶意内容）
        remove_pis=True,             # 移除处理指令
        encoding='utf-8',
    )
    return parser


# ============ 预处理：剥离危险结构 ============

def preprocess_svg(svg_content: str) -> str:
    """
    第一道防线：在 XML 解析前剥离所有危险结构

    即使 lxml 的 resolve_entities=False 有漏洞，
    预处理也能直接消除 Bomb 的源头。
    """

    # 1. 移除 DOCTYPE 声明（实体定义的载体）
    #    <!DOCTYPE svg [ <!ENTITY x0 "bomb"> <!ENTITY x1 "&x0;&x0;"> ]>
    svg_content = re.sub(
        r'<!DOCTYPE\s+\w+\s*\[[^\]]*\]\s*>',
        '', svg_content, flags=re.DOTALL
    )
    svg_content = re.sub(
        r'<!DOCTYPE\s+\w+\s+PUBLIC\s+[^>]*>',
        '', svg_content
    )
    svg_content = re.sub(
        r'<!DOCTYPE\s+\w+\s+SYSTEM\s+[^>]*>',
        '', svg_content
    )
    svg_content = re.sub(
        r'<!DOCTYPE\s+\w+\s*>',
        '', svg_content
    )

    # 2. 移除所有 ENTITY 声明（Bomb 的核心组件）
    svg_content = re.sub(r'<!ENTITY\s+\w+\s+[^>]*>', '', svg_content)

    # 3. 移除 <script> 标签（防 XSS）
    svg_content = re.sub(
        r'<script[^>]*>.*?</script>',
        '', svg_content, flags=re.DOTALL | re.IGNORECASE
    )

    # 4. 移除事件处理属性（onclick, onload 等）
    svg_content = re.sub(
        r'\s+on\w+\s*=\s*"[^"]*"',
        '', svg_content, flags=re.IGNORECASE
    )
    svg_content = re.sub(
        r'\s+on\w+\s*=\s*\'[^\']*\'',
        '', svg_content, flags=re.IGNORECASE
    )

    # 5. 移除外部 href/xlink:href（防 SSRF）
    svg_content = re.sub(
        r'\s+(?:xlink:)?href\s*=\s*"[^"]*(?:http|ftp)[^"]*"',
        '', svg_content, flags=re.IGNORECASE
    )

    # 6. 移除 <?xml-stylesheet?> 处理指令
    svg_content = re.sub(r'<\?xml-stylesheet[^?]*\?>', '', svg_content)

    # 7. 移除注释（可能藏恶意内容）
    svg_content = re.sub(r'<!--.*?-->', '', svg_content, flags=re.DOTALL)

    # 8. 检测实体引用残留 &xxx;（预处理后不应存在）
    entity_refs = re.findall(r'&(\w+);', svg_content)
    # 允许的 XML 预定义实体: amp, lt, gt, quot, apos
    dangerous_refs = [ref for ref in entity_refs
                      if ref not in ('amp', 'lt', 'gt', 'quot', 'apos')]
    if dangerous_refs:
        raise SVGSecurityError(
            f"Custom entity references found: {dangerous_refs}. "
            f"Possible XML Bomb residue after preprocessing."
        )

    return svg_content


# ============ 完整校验流程 ============

def validate_base64_svg(b64_string: str) -> dict:
    """
    完整的 Base64 SVG 安全校验流程（不依赖 defusedxml）

    返回:
        dict: {"valid": True, "size": int, "elements": dict}
        元素统计: {"svg": 1, "circle": 2, ...}

    异常:
        SVGSecurityError: 校验失败时抛出
    """

    # ===== L1: Base64 基础校验 =====
    if not b64_string or not isinstance(b64_string, str):
        raise SVGSecurityError("Empty or invalid base64 input")

    if len(b64_string) > MAX_BASE64_SIZE:
        raise SVGSecurityError(
            f"Base64 too large: {len(b64_string)} chars (max {MAX_BASE64_SIZE})"
        )

    # 提取纯 Base64 数据（处理 data URI 前缀）
    b64_data = b64_string
    if b64_string.startswith('data:'):
        try:
            b64_data = b64_string.split(',', 1)[1]
        except IndexError:
            raise SVGSecurityError("Invalid data URI format")

    # Base64 格式校验
    try:
        decoded_bytes = base64.b64decode(b64_data, validate=True)
    except Exception as e:
        raise SVGSecurityError(f"Invalid base64 encoding: {e}")

    # ===== L2: 解码后大小校验 =====
    decoded_size = len(decoded_bytes)
    if decoded_size > MAX_DECODED_SIZE:
        raise SVGSecurityError(
            f"Decoded SVG too large: {decoded_size} bytes (max {MAX_DECODED_SIZE})"
        )

    # UTF-8 解码
    try:
        svg_content = decoded_bytes.decode('utf-8')
    except UnicodeDecodeError:
        raise SVGSecurityError("SVG content is not valid UTF-8")

    # ===== L3: 预处理剥离 =====
    svg_content = preprocess_svg(svg_content)

    # ===== L4: lxml 安全解析 =====
    parser = create_safe_parser()
    try:
        tree = etree.fromstring(svg_content.encode('utf-8'), parser)
    except etree.XMLSyntaxError as e:
        raise SVGSecurityError(f"SVG parsing failed: {e}")

    # 额外检查：lxml 解析后不应有实体节点残留
    for elem in tree.iter():
        if isinstance(elem, etree.EntityDecl):
            raise SVGSecurityError(
                "Entity declaration found in parsed tree - possible Bomb"
            )

    # ===== L5: SVG 根元素验证 =====
    root_tag = tree.tag
    local_tag = root_tag
    if root_tag.startswith('{'):
        ns, local_tag = root_tag[1:].split('}', 1)
        if ns != SVG_NS:
            raise SVGSecurityError(f"Wrong namespace: {ns}")

    if local_tag != 'svg':
        raise SVGSecurityError(f"Root element is not <svg>: got <{local_tag}>")

    # ===== L6: 白名单校验 =====
    validate_svg_whitelist(tree)

    # ===== L7: 嵌套深度 + 元素数量 =====
    check_nesting_depth(tree)
    check_element_count(tree)

    # ===== L8: 危险属性值 =====
    check_dangerous_attribute_values(tree)

    # 校验通过
    return {
        "valid": True,
        "size": decoded_size,
        "elements": count_elements_by_type(tree),
    }


def validate_svg_whitelist(tree):
    """白名单校验：只允许安全的 SVG 元素和属性"""
    for elem in tree.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith('{'):
            tag = tag.split('}', 1)[1]

        if tag not in ALLOWED_SVG_ELEMENTS:
            raise SVGSecurityError(f"Disallowed SVG element: <{tag}>")

        for attr_name, attr_value in elem.attrib.items():
            local_attr = attr_name
            if attr_name.startswith('{'):
                local_attr = attr_name.split('}', 1)[1]

            if local_attr not in ALLOWED_ATTRIBUTES:
                raise SVGSecurityError(
                    f"Disallowed attribute '{local_attr}' on <{tag}>"
                )

            # 属性值长度限制
            if len(attr_value) > MAX_ATTR_VALUE_LENGTH:
                raise SVGSecurityError(
                    f"Attribute '{local_attr}' value too long: "
                    f"{len(attr_value)} chars (max {MAX_ATTR_VALUE_LENGTH})"
                )


def check_nesting_depth(tree):
    """检查嵌套深度"""
    def _depth(elem, current=0):
        if current > MAX_NESTING_DEPTH:
            raise SVGSecurityError(
                f"Nesting depth exceeds limit: {current} (max {MAX_NESTING_DEPTH})"
            )
        for child in elem:
            _depth(child, current + 1)
    _depth(tree, 0)


def check_element_count(tree):
    """检查元素总数"""
    count = sum(1 for _ in tree.iter())
    if count > MAX_ELEMENT_COUNT:
        raise SVGSecurityError(
            f"Too many elements: {count} (max {MAX_ELEMENT_COUNT})"
        )


def check_dangerous_attribute_values(tree):
    """检查属性值中的危险内容"""
    DANGEROUS_PATTERNS = [
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

    for elem in tree.iter():
        for attr_name, attr_value in elem.attrib.items():
            for pattern, description in DANGEROUS_PATTERNS:
                if re.search(pattern, attr_value, re.IGNORECASE):
                    raise SVGSecurityError(
                        f"Dangerous content in '{attr_name}': {description}"
                    )


def count_elements_by_type(tree):
    """统计元素类型"""
    counts = {}
    for elem in tree.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith('{'):
            tag = tag.split('}', 1)[1]
        counts[tag] = counts.get(tag, 0) + 1
    return counts


# ============ 获取安全 SVG 字符串 ============

def get_safe_svg_string(b64_string: str) -> str:
    """
    校验通过后，返回安全的 SVG 字符串（已剥离危险内容）
    可直接用于存储或渲染
    """
    # 先校验
    result = validate_base64_svg(b64_string)

    # 重新解码和预处理
    b64_data = b64_string
    if b64_string.startswith('data:'):
        b64_data = b64_string.split(',', 1)[1]

    decoded_bytes = base64.b64decode(b64_data, validate=True)
    svg_content = decoded_bytes.decode('utf-8')
    svg_content = preprocess_svg(svg_content)

    parser = create_safe_parser()
    tree = etree.fromstring(svg_content.encode('utf-8'), parser)

    # 序列化为干净的 SVG
    safe_svg = etree.tostring(
        tree,
        pretty_print=True,
        encoding='unicode',
        method='xml',
    )

    return safe_svg