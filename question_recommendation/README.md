# question_recommendation

基于“共享错误码 + 最小推荐上下文 + 内置能力卡 + LLM 自然表达”的问数推荐模块。

```text
上一步结构化意图 + ErrorInfo + llm_refuse_message
→ build_recommendation_context
→ RecommendationContext
→ 确定性能力卡过滤与 Top 12 排序
→ Chat LLM 生成自然问题
```

能力卡召回和排序不调用 LLM 或 Embedding。最终 LLM 输出只校验 JSON 结构，不执行
内容过滤、去重、补足或改写。

## 快速使用

上游和推荐模块必须共同使用 `query_errors.ErrorInfo`，不接受字典或其他同形对象。

```python
from query_errors import ErrorCode
from question_recommendation import (
    build_recommendation_context,
    recommend_questions_chat,
)

upstream_result = {
    "intention": "查指标",
    "question": "查询 IP 以 10.1 开头的设备平均 CPU 利用率",
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

`llm_refuse_message` 只辅助最终 LLM 理解本次详细原因，不参与错误分类或无效值提取。

## 共享错误类型

`query_errors` 包提供完整统一错误定义：

```python
from query_errors import ErrorCode, ErrorCodeLike, ErrorInfo, ErrorLevel, ErrorStage
```

推荐分类只依赖稳定的 `ErrorInfo.key`。未知错误码、第 1 类和第 7～11 类错误统一使用
`basic` 基础推荐，不根据 `message` 猜测错误类型。

| `ErrorInfo` 字段 | 含义 |
|---|---|
| `key` | 稳定错误码，是推荐分类的唯一依据 |
| `level` | `info`、`warning` 或 `error` |
| `stage` | 错误发生阶段，仅用于统一错误协议，不保存到推荐上下文 |
| `message` | 稳定用户说明，透传为 `refusal_message` |

## RecommendationContext

`RecommendationContext` 是推荐模块唯一消费的标准上下文，不保存上一步全部结构。

| 字段 | 类型 | 含义与用途 |
|---|---|---|
| `intention` | `str` | 查信息、查告警、查指标或查链路；用于能力过滤和排序 |
| `question` | `str` | 用户原始问题；供 LLM 保持原查询方向 |
| `device_types` | `list[str]` | 设备类型；用于对象和业务域匹配 |
| `subcomponent_types` | `list[str]` | 接口、光模块等主要子部件对象 |
| `identifiers` | `list[Identifier]` | 仍有效、允许继承的 IP、MAC、名称等定位条件 |
| `properties` | `list[str]` | 查询属性；用于属性能力匹配 |
| `kpis` | `list[str]` | 查询指标；用于指标能力匹配 |
| `time` | `str` | 时间原始表达 |
| `alarm` | `AlarmCondition \| None` | 告警类型和值 |
| `aggregations` | `list[str]` | 规范化聚合算子 |
| `tables` | `list[str]` | 逻辑表名；用于加载元数据并辅助排序 |
| `recovery_strategy` | `str` | 根据 `ErrorInfo.key` 确定的恢复策略 |
| `refusal_message` | `str` | `ErrorInfo.message` 提供的稳定说明 |
| `refusal_detail` | `str` | 本次 LLM 拒答详情，仅辅助表达 |
| `invalid_values` | `list[str]` | 已确认无效、禁止推荐问题继承的值 |

上下文支持 `to_dict`、`to_json`、`from_dict` 和 `from_json`。

`Identifier` 字段：

| 字段 | 含义 |
|---|---|
| `value` | IP、MAC、名称或其他设备定位值 |
| `id_type` | `IP`、`MAC`、`NAME` 或 `OTHER` |
| `match_mode` | `EXACT`、`PREFIX`、`SUFFIX` 或 `FUZZY` |

`AlarmCondition` 字段：

| 字段 | 含义 |
|---|---|
| `alarm_type` | `NAME`、`LEVEL` 或 `STATUS` |
| `alarm_value` | 告警名称、级别或状态值 |

## 上一步结构转换

```python
build_recommendation_context(
    upstream_result,
    refuse_info=None,
    llm_refuse_message="",
)
```

转换行为：

- `refuse_info` 只接受共享 `ErrorInfo` 或 `None`，否则抛出 `TypeError`。
- `llm_refuse_message` 只接受字符串。
- 无拒答信息时保持普通推荐，`recovery_strategy` 为空。
- 只有 `llm_refuse_message`、未知错误码或未专门处理阶段时使用 `basic`。
- 忽略 `tenant`、`subnet`、`subcomponents[].subcomponent_name`、`link_relation` 和未知字段。
- `count(distinct)` 规范为 `count_distinct`，`topN` 规范为 `top_n`。

### 无效值

无效值只根据错误码规则从结构化意图获取，不从拒答文案提取：

| 失效规则 | 处理 |
|---|---|
| `all_device_identifiers` | 移除所有 `devices[].device_id` |
| `ip_identifiers` | 仅移除 `id_type == "IP"` 的设备标识 |
| `name_identifiers` | 仅移除 `id_type == "NAME"` 的设备标识 |
| `all_kpis` | 移除所有 `kpis` |

移除的值加入 `invalid_values`，Prompt 禁止从用户原问题或详细拒答信息中重新继承。
多候选场景保留原值，以便最终问题帮助用户消歧。

## 恢复策略

| 策略 | 推荐行为 |
|---|---|
| `basic` | 已识别对象的列表、数量、基础信息、属性信息或概览 |
| `clarify` | 补齐对象、指标、时间、过滤条件或聚合参数 |
| `disambiguate` | 明确业务域、父对象、设备类型或具体方向 |
| `remove_invalid` | 移除无效定位值或 KPI，推荐不依赖无效值的问题 |
| `reframe` | 推荐更简单、拆分后或改变查询路径的同对象问题 |
| `adjust_scope` | 保留原查询方向并放宽或缩小范围 |

第 2 类 `intent_reject_*` 统一使用 `basic`。第 3～6 类的具体映射集中定义在
`refusal_rules.py` 的 `REFUSAL_RECOVERY_RULES`，未配置错误码同样使用 `basic`。

| 错误码类别或关键错误码 | 策略 |
|---|---|
| `intent_reject_*` | `basic` |
| 跨域、设备类型不一致、多候选、值语义歧义 | `disambiguate` |
| 设备/IP/名称/KPI 不存在 | `remove_invalid` |
| 未配置的 `intent_clarify_*` | `clarify` |
| 不支持子网查询、关系/字段失败、别名规范化失败、主要 SQL 生成失败 | `reframe` |
| `sql_generation_timeout` | `adjust_scope` |
| 未配置错误码 | `basic` |

## CapabilityCard

内置能力卡位于 `data/capability_cards.json`。能力卡定义“系统允许推荐什么”，
`golden_questions` 仅指导表达，不是固定输出模板。

| 字段 | 含义 |
|---|---|
| `capability_id` | 唯一能力标识 |
| `domain` | 网络、服务器、存储、PON、终端等业务域 |
| `intent_type` | 支持的查询意图 |
| `objects` / `parent_object` | 支持的主要对象和父对象 |
| `locators` | 支持的定位值类型 |
| `attribute_policy` / `metric_policy` | 属性和指标支持策略 |
| `aggregations` | 支持的聚合算子 |
| `result_forms` | 列表、数量、基础信息、趋势等结果形态 |
| `time_policy` | 时间是否必填、可选或不适用 |
| `recovery_strategies` | 支持的恢复策略 |
| `table_hints` | 逻辑表和元数据相关度提示 |
| `golden_questions` | 提供给 LLM 的自然问题示例 |
| `priority` | 静态排序优先级 |

属性和指标策略的 `mode`：

- `allow`：只允许 `allow` 数组中明确声明的值。
- `dynamic`：允许根据逻辑模型元数据表达。
- `dynamic_inherit`：允许继承上下文中仍有效的原查询值。
- `none`：能力卡不提供该类能力。

`basic` 只召回信息能力卡。其他策略按意图、主要对象、明确业务域、KPI、属性、
聚合算子和策略边界执行硬过滤，再结合逻辑表元数据做确定性排序。

设备类型能够唯一确定业务域时，始终过滤其他领域。没有设备类型且策略为
`disambiguate` 时，可保留同一对象的多个领域能力卡，最终问题必须明确父设备或领域。

```python
from question_recommendation import recommend_capabilities

ranked = recommend_capabilities(context, metadata_tables=[], limit=12)
```

## 逻辑模型元数据

推荐器根据 `context.tables` 和 `logical_model_path_provider` 读取：

```text
{logical_model_path}/{table_name}.logical.yaml
```

只提取表名 `name`、表描述 `description_cn`，以及 `schema.fields` 中每个字段的
`name` 和 `description_cn`。加载结果直接按表组织为 `MetadataTable`，只影响排序和
LLM 表达，不参与业务域硬过滤。

## Chat 接口与输出

```python
recommend_questions_chat(
    context,
    llm_chat_client,
    logical_model_path_provider=None,
)
```

结构合法时直接返回：

```json
{
  "recommends": ["推荐问题1", "推荐问题2", "推荐问题3"],
  "explain": "先查看可用设备，再选择具体设备继续查询。"
}
```

`explain` 是直接展示给用户的一句话建议。Prompt 要求它友好、可执行、不责备用户，
且不暴露错误码、恢复策略、能力卡、评分、表名或无效值。普通场景说明推荐内容与
用户关注方向的关系；拒答场景则提示用户下一步如何补充、选择、拆分或调整范围。

无法解析或结构不合法时返回 `{"recommends": [], "explain": ""}`。

安装 YAML 依赖并运行测试：

```bash
pip install -r question_recommendation/requirements.txt
python3 -m pytest question_recommendation/tests query_errors/tests -q
```
