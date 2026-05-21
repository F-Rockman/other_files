"""
图片安全校验配置常量

可通过修改此文件调整校验阈值和白名单。
"""

# ============ 大小限制 ============

MAX_FILE_SIZE = 10 * 1024 * 1024            # 单个图片文件最大 10MB
MAX_BASE64_SIZE = 14 * 1024 * 1024          # Base64 最大 ~14MB（膨胀率 ~1.37x）
MAX_PIXELS = 100_000_000                    # 像素总数上限（约 10000×10000）
MAX_DIMENSION = 65535                       # 单边最大像素数

# ============ 格式白名单 ============

SAFE_FORMATS = {'PNG', 'JPEG', 'GIF', 'BMP', 'WEBP'}

FORMAT_SIGNATURES = {
    'PNG':  b'\x89PNG\r\n\x1a\n',
    'JPEG': b'\xff\xd8\xff',
    'GIF87a': b'GIF87a',
    'GIF89a': b'GIF89a',
    'BMP':  b'BM',
    'WEBP': b'RIFF',
}

FORMAT_MIME_MAP = {
    'PNG':  'image/png',
    'JPEG': 'image/jpeg',
    'GIF':  'image/gif',
    'BMP':  'image/bmp',
    'WEBP': 'image/webp',
}

# ============ Polyglot 检测 ============

DANGEROUS_SIGNATURES = {
    'PDF':  b'%PDF',
    'ZIP':  b'PK\x03\x04',
    'RAR':  b'Rar!\x1a\x07',
    '7Z':   b'7z\xbc\xaf\x27\x1c',
    'EXE':  b'MZ',
    'HTML': (b'<html', b'<!DOCTYPE', b'<HTML'),
    'PHP':  b'<?php',
    'JS':   None,
}

DANGEROUS_TAIL_PATTERNS = [
    b'<?php', b'<?=',
    b'<script', b'<SCRIPT',
    b'<%', b'<%=', b'<%@',
    b'eval(', b'exec(', b'system(', b'passthru(',
    b'shell_exec(', b'popen(',
    b'javascript:', b'JAVASCRIPT:',
    b'vbscript:', b'VBSCRIPT:',
]

# ============ EXIF 安全字段 ============

SAFE_EXIF_TAGS = {
    256: 'ImageWidth',
    257: 'ImageLength',
    272: 'Model',
    274: 'Orientation',
    282: 'XResolution',
    283: 'YResolution',
    296: 'ResolutionUnit',
    305: 'Software',
    306: 'DateTime',
    36867: 'DateTimeOriginal',
    36868: 'DateTimeDigitized',
}

DANGEROUS_EXIF_PATTERNS = [
    r'<script',
    r'javascript:',
    r'vbscript:',
    r'on\w+\s*=',
    r'\?php',
    r'eval\s*\(',
    r'http://',
    r'https://',
    r'ftp://',
]

# ============ 隐写术检测 ============

STEGANOGRAPHY_LSB_THRESHOLD = 0.05         # LSB 分布偏离阈值（正常 ≈ 0.5）
STEGANOGRAPHY_SAMPLE_PIXELS = 10000        # 采样像素数