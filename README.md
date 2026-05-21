# python_utils

存放各种不同目的的 Python 工具模块。

## 目录结构

```
python_utils/
├── svg_security/          # SVG 向量炸弹 (XML Bomb) 安全校验
│   ├── __init__.py
│   ├── validator.py       # 核心校验逻辑
│   ├── config.py          # 配置常量（阈值、白名单）
│   └── tests/
│       └── test_validator.py
└── ...                    # 更多工具模块
```

## svg_security - SVG 安全校验

防护 SVG 向量炸弹（XML Bomb / Billion Laughs Attack），适用于后端接收 Base64 编码 SVG 的场景。

### 防护层级

| 层级 | 防护手段 | 防护目标 |
|------|----------|----------|
| L1 | Base64 格式/大小校验 | 防止超大输入 |
| L2 | 解码后大小校验 | 防止膨胀攻击 |
| L3 | 预处理剥离 DOCTYPE/ENTITY | 直接消除 Bomb 源头 |
| L4 | lxml 安全解析器 | 禁用实体扩展/外部引用 |
| L5 | SVG 根元素验证 | 防止非 SVG XML |
| L6 | 元素/属性白名单 | 防止 script、XSS |
| L7 | 嵌套深度/元素数量限制 | 防止深度嵌套攻击 |
| L8 | 危险属性值检测 | 防止 javascript: URI |

### 快速使用

```python
from svg_security import validate_base64_svg, SVGSecurityError

# 校验 Base64 SVG
try:
    result = validate_base64_svg(base64_string)
    print(f"Valid! Size: {result['size']} bytes")
except SVGSecurityError as e:
    print(f"Blocked: {e}")
```

### FastAPI 集成

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from svg_security import validate_base64_svg, SVGSecurityError

app = FastAPI()

class SVGUpload(BaseModel):
    svg_base64: str

@app.post("/api/svg/upload")
async def upload_svg(data: SVGUpload):
    try:
        result = validate_base64_svg(data.svg_base64)
        return {"status": "ok", "size": result["size"]}
    except SVGSecurityError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

### 依赖

- lxml

### 不依赖

- defusedxml（使用 lxml 安全配置 + 预处理双重防线替代）

## 运行测试

```bash
cd python_utils
pip install lxml pytest
pytest svg_security/tests/
```