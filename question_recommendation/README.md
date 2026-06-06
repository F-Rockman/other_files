# question_recommendation

基于“最小化推荐上下文 + 内置能力卡 + LLM 自然表达”的问数推荐模块。

推荐链路：

```text
上一步意图识别结果
→ build_recommendation_context
→ RecommendationContext
→ 确定性能力卡过滤与 Top 12 排序
→ 加载逻辑表元数据
→ Chat LLM 生成自然问题
```

能力卡召回和排序不调用 LLM 或 Embedding。最终 LLM 输出只校验 JSON 结构，不执行
内容过滤、去重、补足或改写。

## 快速使用

```python
from question_recommendation import (
    build_recommendation_context,
    recommend_questions_chat,
)

upstream_result = {
    "intention": "查指标",
    "question": "查询 IP 以 10.1 开头的设备平均 CPU 利用率",
    "devices": [
        {
            "device_id": "10.1",
            "id_type": "IP",
            "match_mode": "PREFIX",
            "device_type": "网络设备",
        }
    ],
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
    failure_reason="IP 前缀匹配到多个设备",
)

result = recommend_questions_chat(
    context,
    llm_chat_client=my_llm_chat_client,
    logical_model_path_provider=lambda: "/data/logical-models",
)
```

## RecommendationContext

`RecommendationContext` 是推荐模块唯一消费的标准上下文。它不是上一步结构的镜像，
只保存召回、排序、元数据加载和 LLM 表达实际使用的字段。

| 字段 | 类型 | 含义与用途 |
|---|---|---|
| `intention` | `str` | 查信息、查告警、查指标或查链路；用于能力卡过滤和排序 |
| `question` | `str` | 用户原始问题；仅供 LLM 保持原始方向和表达 |
| `device_types` | `list[str]` | 明确设备类型；用于设备对象匹配，并限定子部件所属父对象 |
| `subcomponent_types` | `list[str]` | 接口、光模块等子部件类型；存在时作为主要查询对象 |
| `identifiers` | `list[Identifier]` | 仍有效、允许继承的 IP、MAC、名称等定位条件 |
| `properties` | `list[str]` | 查询属性；用于匹配属性能力 |
| `kpis` | `list[str]` | 查询指标；用于匹配指标能力 |
| `time` | `str` | 时间原始表达；用于时间策略匹配和 LLM 表达 |
| `alarm` | `AlarmCondition \| None` | 告警类型和值；用于告警能力匹配 |
| `aggregations` | `list[str]` | 规范化聚合算子；用于聚合能力匹配 |
| `tables` | `list[str]` | 逻辑表名；用于加载元数据并辅助排序 |
| `failure_type` | `str` | 标准失败恢复类型；为空表示普通推荐 |
| `failure_summary` | `str` | 提供给 LLM 的业务失败说明 |
| `invalid_values` | `list[str]` | 已明确失败、禁止继续继承的条件值 |

### Identifier

| 字段 | 含义 |
|---|---|
| `value` | IP、MAC、名称或其他定位值 |
| `id_type` | `IP`、`MAC`、`NAME`、`OTHER` |
| `match_mode` | `EXACT`、`PREFIX`、`SUFFIX`、`FUZZY` |

### AlarmCondition

| 字段 | 含义 |
|---|---|
| `alarm_type` | `NAME`、`LEVEL` 或 `STATUS` |
| `alarm_value` | 告警名称、级别或状态值 |

上下文支持序列化：

```python
context.to_dict()
context.to_json()
RecommendationContext.from_dict(data)
RecommendationContext.from_json(text)
```

## 上一步结构转换

```python
build_recommendation_context(
    upstream_result,
    failure_reason="",
    failure_detail="",
)
```

字段映射：

| 上一步字段 | 内部字段 |
|---|---|
| `intention` | `intention` |
| `question` | `question` |
| `devices[].device_type` | `device_types` |
| `devices[].device_id/id_type/match_mode` | `identifiers` |
| `subcomponents[].subcomponent_type` | `subcomponent_types` |
| `properties` | `properties` |
| `kpis` | `kpis` |
| `time` | `time` |
| `alarm` | `alarm` |
| `agg` | `aggregations` |
| `tables` | `tables` |
| 失败原因和详情 | `failure_type`、`failure_summary`、`invalid_values` |

以下字段当前没有稳定的推荐用途，因此完全忽略：

```text
tenant
subnet
subcomponents[].subcomponent_name
link_relation
其他未知字段
```

聚合算子转换：

```text
count(distinct) → count_distinct
topN → top_n
其他算子统一转成小写
```

标准失败类型包括：业务域不明确、匹配到多设备、指标不支持、属性不支持、父对象定位
失败、对象定位失败、时间缺失、条件过细、无结果、内部执行异常、其他失败。

## CapabilityCard

内置能力卡配置位于 `data/capability_cards.json`。能力卡定义“系统允许推荐什么”，
`golden_questions` 仅指导表达，不是固定输出模板。

| 字段 | 含义 |
|---|---|
| `capability_id` | 唯一能力标识 |
| `domain` | 网络、服务器、存储、PON、终端等业务域 |
| `intent_type` | 支持的查询意图 |
| `objects` | 支持的主要查询对象 |
| `parent_object` | 子对象所属父设备 |
| `locators` | 支持的定位值类型 |
| `attribute_policy` | 属性支持策略 |
| `metric_policy` | 指标支持策略 |
| `aggregations` | 支持的聚合算子 |
| `result_forms` | 列表、数量、基础信息、趋势等结果形态 |
| `time_policy` | 时间是否必填、可选或不适用 |
| `recovery_types` | 支持处理的失败类型 |
| `table_hints` | 逻辑表和表描述相关度提示 |
| `golden_questions` | 提供给 LLM 的自然问题示例 |
| `priority` | 静态排序优先级 |

指标与属性策略：

- `allow`：仅支持 `allow` 数组中明确列出的值。
- `dynamic`：允许根据逻辑元数据动态表达。
- `dynamic_inherit`：允许继承原问题中的值；失败类型为指标不支持时不会继承。
- `none`：该能力卡不是对应类型的查询能力。

## 确定性推荐算法

可以独立查看算法选出的能力卡：

```python
from question_recommendation import recommend_capabilities

ranked = recommend_capabilities(context, metadata_tables=[], limit=12)
for item in ranked:
    print(item.card.capability_id, item.match_score, item.match_reasons)
```

算法仅在明确冲突时硬过滤：

- 查询意图冲突，且能力卡不支持当前失败恢复类型；
- 主要查询对象冲突；
- 明确设备类型唯一对应的业务域冲突；
- 能力卡明确不支持当前 KPI、属性或聚合算子；
- 能力卡不支持当前失败恢复类型。

`tables`、表描述和字段描述只影响排序，不参与硬过滤。

### 多领域对象

当 `failure_type == "业务域不明确"` 时，不执行单一领域硬过滤。例如查询光模块接收
功率时：

- 保留支持接收功率的网络光模块指标能力；
- 保留网络光模块信息能力；
- 保留服务器光模块信息能力；
- 不会为不支持接收功率的服务器光模块生成该指标能力候选。

Prompt 要求最终推荐明确父对象，例如“网络设备光模块”或“服务器光模块”。

## 逻辑模型元数据

推荐器根据 `context.tables` 和 `logical_model_path_provider` 读取：

```text
{logical_model_path}/{table_name}.logical.yaml
```

只提取以下内容：

```yaml
name: network_device
description_cn: 网络设备
schema:
  fields:
    - name: device_ip
      description_cn: 设备IP地址
```

`load_logical_metadata` 在读取文件时直接按表组织结果，不再返回平铺列列表：

```python
[
    MetadataTable(
        table_name="network_device",
        table_description="网络设备",
        columns=[
            MetadataColumn(
                column_name="device_ip",
                column_description="设备IP地址",
            )
        ],
    )
]
```

同一张表的字段始终位于该表的 `columns` 中。能力排序和 Prompt 直接消费这个结构，
推荐器不再进行二次分组。

单个文件缺失或格式错误时跳过。目录无效或缺少 PyYAML 时抛出
`LogicalMetadataError`。

## Chat 接口与输出

```python
recommend_questions_chat(
    context,
    llm_chat_client,
    logical_model_path_provider=None,
)
```

输出结构：

```json
{
  "recommends": ["推荐问题1", "推荐问题2", "推荐问题3"],
  "explain": "推荐说明"
}
```

结构合法时直接返回；无法解析或结构不合法时返回：

```json
{"recommends": [], "explain": ""}
```

安装 YAML 依赖并运行测试：

```bash
pip install -r question_recommendation/requirements.txt
python3 -m pytest question_recommendation/tests -q
```
