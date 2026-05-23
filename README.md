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
├── image_security/        # 图片安全校验（解压缩炸弹、Polyglot、EXIF、隐写术）
│   ├── __init__.py
│   ├── validator.py       # 核心校验逻辑
│   ├── config.py          # 配置常量（阈值、白名单）
│   └── tests/
│       └── test_validator.py
├── sql_intent/            # SQL 生成前置意图判断
│   ├── __init__.py
│   ├── prompt.py          # 意图判断 Prompt 文本
│   ├── classifier.py      # 核心判断逻辑（LLM 调用 + JSON 解析）
│   ├── config.py          # 配置常量（默认值、错误消息）
│   └── tests/
│       └── test_classifier.py
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

## image_security - 图片安全校验

防护图片相关攻击（解压缩炸弹、Polyglot、EXIF 注入、隐写术等），适用于后端接收图片文件/字节/Base64 的场景。

### 防护层级

| 层级 | 防护手段 | 防护目标 |
|------|----------|----------|
| L1 | 文件大小校验 | 防止超大输入 |
| L2 | 格式签名验证 | 防止 Polyglot 多语言文件 |
| L3 | Pillow MAX_IMAGE_PIXELS | 防止解压缩炸弹 (Pixel Flood) |
| L4 | 图片尺寸限制 | 防止巨大像素矩阵 |
| L5 | EXIF 元数据清洗 | 防止 XSS/SSRF/路径泄露 |
| L6 | LSB 统计分析 | 检测隐写术 |
| L7 | Content-Type 一致性验证 | 防止格式混淆攻击 |
| L8 | 尾部危险内容检测 | 防止嵌入脚本 (PHP/JS) |

### 快速使用

```python
from image_security import validate_image_file, validate_base64_image, ImageSecurityError

# 校验图片文件
try:
    result = validate_image_file("/path/to/image.png")
    print(f"Valid! Format: {result['format']}, Size: {result['dimensions']}")
except ImageSecurityError as e:
    print(f"Blocked: {e}")

# 校验 Base64 图片
try:
    result = validate_base64_image(base64_string, claimed_mime="image/png")
    print(f"Valid! Format: {result['format']}")
except ImageSecurityError as e:
    print(f"Blocked: {e}")
```

### FastAPI 集成

```python
from fastapi import FastAPI, HTTPException, UploadFile
from image_security import validate_image_file, ImageSecurityError

app = FastAPI()

@app.post("/api/image/upload")
async def upload_image(file: UploadFile):
    try:
        result = validate_image_file(file.file, claimed_mime=file.content_type)
        return {"status": "ok", "format": result["format"], "size": result["size"]}
    except ImageSecurityError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

### 依赖

- Pillow (PIL)

### 不依赖

- ImageMagick（使用 Pillow 纯 Python 解析，不执行命令）

## sql_intent - SQL 生成前置意图判断

判断用户自然语言输入是否应进入 SQL 生成链路。适用于 NL2SQL 系统的前置过滤场景，防止非问数请求、多意图查询、条件不完整等无效输入进入 SQL 生成流程。

### 判断规则

| 规则 | 说明 | 判定 |
|------|------|------|
| R1 | 非问数场景（分析、建议、预测、动作执行等） | reject |
| R2 | 未来尚未发生的数据指标 | reject |
| R3 | 条件不完整（缺少字段、值、范围） | reject |
| R4 | 多意图组合（不同目标、不同维度、混合意图） | reject |
| R5 | 意图不明确 | reject |

单意图扩展认定（仍为 accept）：同一维度+多个指标、同一条件+多个条件值、排名附带派生指标、趋势隐含聚合、对比隐含分组。

### 快速使用

```python
from sql_intent import classify_intent, SQL_INTENT_JUDGMENT_PROMPT

# 使用任意 LLM 客户端（Callable[[str], str]）
def my_llm_client(prompt: str) -> str:
    # 调用你的 LLM SDK（OpenAI、Anthropic、本地模型等）
    return llm_sdk_call(prompt)

result = classify_intent("各省份的销售额和订单数", my_llm_client)
# {"intention": "accept", "reason": ""}

result = classify_intent("帮我分析销量下滑的原因", my_llm_client)
# {"intention": "reject", "reason": "非问数场景"}
```

### FastAPI 集成

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sql_intent import classify_intent, SQLIntentError

app = FastAPI()

class QueryRequest(BaseModel):
    user_input: str

@app.post("/api/sql/intent")
async def check_intent(data: QueryRequest):
    try:
        result = classify_intent(data.user_input, my_llm_client)
        if result["intention"] == "reject":
            raise HTTPException(status_code=400, detail=result["reason"])
        return {"status": "ok", "intention": result["intention"]}
    except SQLIntentError as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 依赖

- 无外部依赖（LLM 客户端由使用者自行提供）

## 运行测试

```bash
pip install lxml Pillow pytest
pytest svg_security/tests/ image_security/tests/ sql_intent/tests/
```