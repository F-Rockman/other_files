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
├── slang_normalizer/      # 黑化改写三层管线（LLM + 最长匹配 + 分词）
│   ├── __init__.py
│   ├── normalizer.py      # 核心改写逻辑（L1 AC + L2 jieba + L3 LLM）
│   ├── prompt.py          # LLM 判断 Prompt（slang/literal/substring）
│   ├── config.py          # 配置常量
│   └── tests/
│       └── test_normalizer.py
├── question_recommendation/ # 六类能力规格问数推荐
│   ├── __init__.py
│   ├── README.md          # 输入字段、必填性、缺失影响和示例
│   ├── prompt.py          # 推荐 Prompt 兼容导出
│   ├── prompt.yaml        # 推荐问题生成 Prompt 文本与 user 片段模板
│   ├── models.py          # 推荐上下文、能力规格、元数据模型
│   ├── capabilities.py    # 六类查询骨架的确定性召回与排序
│   ├── data/              # 设备与特殊能力规格
│   ├── logical_model_reader.py # 根据逻辑表名读取 .logical.yaml
│   ├── recommender.py     # Chat LLM 调用与 JSON 结构解析
│   ├── config.py          # 配置常量
│   ├── requirements.txt   # 推荐模块依赖
│   └── tests/
│       └── test_recommender.py
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

提供两种调用方式：
- **`classify_intent`** — Completion API 版本，将 system 规则与用户输入拼接为单一 prompt 字符串
- **`classify_intent_chat`** — Chat API 版本，将规则作为 system 消息、用户输入作为 user 消息分别发送

Chat API 版本的优势：
- **LLM 缓存**：system 消息不变时可被 LLM 服务端缓存，减少重复 token 计算
- **更高规则遵循度**：system/user 分离后，LLM 对 system 规则的遵循度通常更高

### 判断规则

| 规则 | 说明 | 判定 |
|------|------|------|
| R1 | 非问数场景（分析、建议、预测、动作执行等） | reject |
| R2 | 未来尚未发生的数据指标 | reject |
| R3 | 条件不完整（缺少字段、值、范围） | reject |
| R4 | 多意图组合（不同目标、不同维度、混合意图） | reject |
| R5 | 意图不明确 | reject |

### 展示形式兼容矩阵

判断多意图的核心标准是"能否在一条 SELECT 中并列输出"。以下矩阵展示各展示形式之间的结构兼容性：

| | 明细 | 数量(标量) | 趋势 | 排名 | 对比 | 占比 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **明细** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **数量(标量)** | ❌ | ✅ | ❌ | ✅* | ✅* | ✅* |
| **趋势** | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **排名** | ❌ | ✅* | ❌ | ✅ | ❌ | ❌ |
| **对比** | ❌ | ✅* | ❌ | ❌ | ✅ | ✅* |
| **占比** | ❌ | ✅* | ❌ | ❌ | ✅* | ✅ |

✅ = 可在同一 SELECT 中并列输出（单意图）
❌ = 结构不兼容，需要不同 SQL（多意图）
✅* = 通过派生兼容（排名/对比/占比是同一 GROUP BY 的派生列）

单意图扩展认定（仍为 accept）：同一维度+多个指标、同一条件+多个条件值、排名附带派生指标、趋势隐含聚合、对比隐含分组。

### 快速使用

**Completion API 版本**（`classify_intent`）：

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

**Chat API 版本**（`classify_intent_chat`）：

```python
from sql_intent import classify_intent_chat, SQL_INTENT_SYSTEM_PROMPT

# 使用任意 LLM Chat 客户端（Callable[[list[dict]], str]）
def my_llm_chat_client(messages: list[dict]) -> str:
    # 调用你的 LLM Chat SDK，传入消息列表
    return llm_chat_sdk_call(messages)

result = classify_intent_chat("各省份的销售额和订单数", my_llm_chat_client)
# {"intention": "accept", "reason": ""}

result = classify_intent_chat("帮我分析销量下滑的原因", my_llm_chat_client)
# {"intention": "reject", "reason": "非问数场景"}
```

### FastAPI 集成

**Completion API 版本**：

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

**Chat API 版本**：

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sql_intent import classify_intent_chat, SQLIntentError

app = FastAPI()

class QueryRequest(BaseModel):
    user_input: str

@app.post("/api/sql/intent/chat")
async def check_intent_chat(data: QueryRequest):
    try:
        result = classify_intent_chat(data.user_input, my_llm_chat_client)
        if result["intention"] == "reject":
            raise HTTPException(status_code=400, detail=result["reason"])
        return {"status": "ok", "intention": result["intention"]}
    except SQLIntentError as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 依赖

- 无外部依赖（LLM 客户端由使用者自行提供）

## question_recommendation - 结构化模板问数推荐

用于问数成功或失败后的推荐问题生成。推荐链路采用“最小化推荐上下文 + 内置能力卡 +
确定性 Top 12 召回 + LLM 自然表达”方案。

完整字段说明、必填性、缺失影响和多设备失败示例见
[`question_recommendation/README.md`](question_recommendation/README.md)。

仅提供 Chat API 调用方式：**`recommend_questions_chat`**。

### 快速使用

```python
from query_errors import ErrorCode
from question_recommendation import build_recommendation_context, recommend_questions_chat

context = build_recommendation_context(
    {
        "intention": "查信息",
        "question": "查询 IP 为 1.1.1.1 的网络设备接口",
        "devices": [{
            "device_id": "1.1.1.1",
            "id_type": "IP",
            "match_mode": "EXACT",
            "device_type": "网络设备",
        }],
        "subcomponents": [{"subcomponent_type": "接口"}],
        "tables": ["network_device", "network_interface"],
    },
    refuse_info=ErrorCode.INTENT_GUIDE_DEVICE_NOT_FOUND.to_info(),
    llm_refuse_message="未找到设备 IP 为 1.1.1.1",
)

def my_llm_chat_client(messages: list[dict]) -> str:
    return llm_chat_sdk_call(messages)

result = recommend_questions_chat(
    context,
    my_llm_chat_client,
    logical_model_dir="/data/logical-models",
)
# {"recommends": [...], "explain": "..."}
```

### 输入边界

- `RecommendationContext` 只保存推荐真正使用的标准字段，由
  `build_recommendation_context` 从上一步结构转换。
- 上游与推荐模块共同使用 `query_errors.ErrorInfo`；恢复策略只由稳定错误 key
  决定，拒答详情不会参与分类或无效值提取。
- 推荐器自动加载内置能力卡，确定性过滤并排序 Top 12；召回过程不调用 LLM 或 Embedding。
- 推荐器根据 `context.tables` 和 `logical_model_dir` 自动读取
  `{table_name}.logical.yaml`，只提取表名、表描述、列名和列描述。
- 调用器只解析 LLM 返回结构，不过滤或补足推荐内容；`recommends`
  输出 1 到 3 条即可，候选不足或质量低时不强行凑满。

### 依赖

- PyYAML（读取 `.logical.yaml`）
- LLM 客户端由使用者自行提供

## slang_normalizer - 黑化改写三层管线

将用户输入中的黑化（网络用语/行业俚语）规范化为标准表达，专为中文子串假阳性场景设计（如"备电"不应匹配"设备电源"中的子串）。

提供两种调用方式：
- **`normalize`** — Completion API 版本
- **`normalize_chat`** — Chat API 版本

### 三层管线

| 层级 | 防护手段 | 防护目标 |
|------|----------|----------|
| L1 | pyahocorasick `iter_long()` 最长匹配 | 优先匹配最长词（compound 赢过 slang） |
| L2 | jieba 分词边界校验 | slang 必须对齐 token 边界才接受 |
| L3 | LLM 兜底判断（slang/literal/substring） | 处理边界情况与歧义场景 |

### 核心机制

**子串假阳性问题**：当黑化词（如"备电"）恰好是某个合法复合词（如"设备电源"）的子串时，简单字符串匹配会将"设备电源"中的"备电"误判为黑化。

**解决方案**：
- **双注册**：同时向 AC 自动机注册 slang 和 compound，各自带类型标签
- **最长匹配**：`iter_long()` 只输出最长词，compound "设备电源"（4字）天然赢过 slang "备电"（2字）
- **边界对齐**：L2 检查 slang 匹配是否对齐 jieba token 边界，不对齐 → 进入 LLM 判断

### 快速使用

**Completion API 版本**（`normalize`）：

```python
from slang_normalizer import normalize, SlangNormalizerError

slang_dict = {
    "备电": "备用电源",
    "yyds": "永远的神",
}
# compound_dict: 包含 slang 子串的合法复合词
compound_dict = {"设备电源", "打电话"}

# 子串假阳性场景：不会误替换"设备电源"中的"备电"
result = normalize("查询服务器设备电源故障", slang_dict, compound_dict)
# {"text": "查询服务器设备电源故障", "matches": [], "unresolved": []}

# 混合场景：compound 保留，standalone slang 被替换
result = normalize("查询服务器设备电源信息，需要备电", slang_dict, compound_dict)
# {"text": "查询服务器设备电源信息，需要备用电源", "matches": [...], "unresolved": []}

# 带 LLM 兜底（处理边界歧义场景）
def my_llm_client(prompt: str) -> str:
    return llm_sdk_call(prompt)

result = normalize("备电系统启动", slang_dict, compound_dict, llm_client=my_llm_client)
```

**Chat API 版本**（`normalize_chat`）：

```python
from slang_normalizer import normalize_chat

def my_llm_chat_client(messages: list[dict]) -> str:
    return llm_chat_sdk_call(messages)

result = normalize_chat("备电系统启动", slang_dict, compound_dict, llm_chat_client=my_llm_chat_client)
```

### 依赖

- pyahocorasick（AC 自动机最长匹配）
- jieba（中文分词边界校验）

## 运行测试

```bash
pip install lxml Pillow pyahocorasick jieba PyYAML pytest
pytest svg_security/tests/ image_security/tests/ sql_intent/tests/ slang_normalizer/tests/ question_recommendation/tests/
```
