# question_recommendation

基于“最小推荐上下文 + 六类通用查询骨架 + 设备能力规格 + LLM 自然表达”的问数推荐模块。

```text
上一步结构化意图 + ErrorInfo + llm_refuse_message
→ build_recommendation_context
→ RecommendationContext
→ 确定性生成并排序 Top 12 候选能力
→ Chat LLM 组合自然问题
```

确定性召回不调用 LLM 或 Embedding。最终 LLM 输出只校验 JSON 结构，不执行内容过滤、
补足或改写。改造只作用于推荐模块，不参与后续 SQL 或问数执行流程。

## 快速使用

```python
from query_errors import ErrorCode
from question_recommendation import (
    build_recommendation_context,
    recommend_questions_chat,
)

upstream_result = {
    "intention": "查指标",
    "question": "查询 IP 以 10.1 开头的网络设备平均 CPU 利用率",
    "devices": [{
        "device_id": "10.1",
        "id_type": "IP",
        "match_mode": "PREFIX",
        "device_type": "网络设备",
    }],
    "subcomponents": [],
    "properties": [],
    "kpis": ["CPU利用率"],
    "time": "",
    "alarm": None,
    "agg": ["avg"],
    "tables": ["network_device", "network_device_metric"],
}

context = build_recommendation_context(
    upstream_result,
    refuse_info=ErrorCode.VALUE_RETRIEVAL_IP_MULTIPLE_CANDIDATES.to_info(),
    llm_refuse_message="IP 前缀匹配到多个设备",
)

result = recommend_questions_chat(
    context,
    llm_chat_client=my_llm_chat_client,
    logical_model_path_provider=lambda: "/data/logical-models",
)
```

## RecommendationContext

`RecommendationContext` 是推荐模块唯一消费的标准上下文，不保存上一步全部结构。

| 字段 | 用途 |
|---|---|
| `intention` | 路由查信息、查指标、查告警或查链路能力 |
| `question` | 保持原查询方向，并判断明确出现的趋势、TopN 数值和方向 |
| `device_types` | 匹配设备规格和限定子部件父对象 |
| `subcomponent_types` | 匹配主要子部件对象 |
| `identifiers` | 仍有效、允许继承的 IP、MAC、名称等定位条件 |
| `properties` / `kpis` | 属性与 KPI 能力匹配 |
| `time` / `alarm` / `aggregations` | 时间、告警和聚合要求 |
| `tables` | 加载逻辑元数据并辅助排序 |
| `recovery_strategy` | 根据共享错误码确定恢复行为 |
| `refusal_message` / `refusal_detail` | 辅助生成用户友好说明 |
| `invalid_values` | 已确认无效、禁止推荐问题继承的值 |

`build_recommendation_context(...)` 只接受共享 `query_errors.ErrorInfo`。错误分类依赖稳定
`ErrorInfo.key`，不会从拒答文案猜测类型或提取无效值。

## 六类查询骨架

设备和子部件的通用查询只使用六类骨架：

| 骨架 | 路由条件 |
|---|---|
| `device_info` | 查信息、无子部件、无 count |
| `device_count` | 查信息、无子部件、有 count 或 count_distinct |
| `device_metric` | 查指标、无子部件 |
| `subcomponent_info` | 查信息、有子部件、无 count |
| `subcomponent_count` | 查信息、有子部件、有 count 或 count_distinct |
| `subcomponent_metric` | 查指标、有子部件 |

过滤、分组、聚合、比较、排序、TopN 和时间是候选能力允许组合的操作，不单独建卡。
告警、链路、子网资源和对象关系保留为特殊能力。

## 设备能力规格

内置规格位于 `data/device_capability_profiles.json`：

- `device_profiles` 定义业务域、设备类型、别名、定位方式、属性、过滤、分组、KPI、
  子部件和逻辑表提示。
- `subcomponents` 嵌套在所属设备规格中；设备与子部件兼容关系以此为唯一事实来源。
- 每个 KPI 独立声明是否支持当前值、趋势、聚合、比较和排名口径。
- `special_capabilities` 定义告警、链路、子网资源和关系能力。
- `examples` 只指导 LLM 表达，不是固定输出模板，也不是当前环境事实。

重要边界：

- 序列号是属性和过滤字段，不是设备定位方式。
- 服务器只有网卡，不提供服务器端口能力。
- 存储池、LUN、文件系统是存储设备子部件。
- 风扇转速和风扇转速百分比只支持趋势。
- 存储总容量只支持当前值。
- TopN 必须由 KPI 支持对应排名口径，且原问题明确给出 N 与排序方向。

```python
from question_recommendation import (
    load_device_capability_profiles,
    load_special_capabilities,
    recommend_capabilities,
    resolve_primary_capability_type,
)
```

## Basic 兜底

`recovery_strategy == "basic"` 时：

- Basic 是无法进一步细分失败类型时使用的通用 error 表达策略，不是基础能力召回模式。
- 确定性召回、能力过滤和排序与 normal 场景完全相同。
- 完整上下文会传给 LLM；除 `invalid_values` 外，允许继承仍有效的对象、定位值、指标、
  属性、时间和业务范围。
- LLM 按“先定位，再收敛”组织推荐，优先列表、数量、基础信息、候选值和范围放宽方向，
  再结合候选能力推荐可继续收敛的原意图问题。
- 只有完全没有兼容候选时，才回退到全局设备信息和数量能力。

其他恢复策略仍由 `refusal_rules.py` 根据共享错误码确定。

## 逻辑模型元数据

推荐器根据 `context.tables` 读取：

```text
{logical_model_path}/{table_name}.logical.yaml
```

只提取表名 `name`、表描述 `description_cn`，以及 `schema.fields` 中字段的 `name` 和
`description_cn`。元数据辅助候选排序和 LLM 理解当前环境真实业务含义，但不能突破
设备能力规格。

## 输出

```json
{
  "recommends": ["推荐问题1", "推荐问题2", "推荐问题3"],
  "explain": "面向用户的下一步建议"
}
```

Prompt 要求生成正好三条语义不同的问题。推荐器仍保持结构合法即原样返回，不对 LLM
返回数量做补齐、截断或拒绝；无法解析时返回 `{"recommends": [], "explain": ""}`。

运行测试：

```bash
python3 -m pytest question_recommendation/tests query_errors/tests -q
```
