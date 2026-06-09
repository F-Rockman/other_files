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

## 核心概念

本模块中的“能力卡”不是固定问题模板，也不会直接作为最终推荐问题输出。

```text
设备能力规格 DeviceCapabilityProfile
或特殊能力规格 SpecialCapabilitySpec
→ 结合 RecommendationContext 动态生成 CapabilityCandidate
→ 硬过滤、打分、排序和多样性裁剪
→ 将排序后的精简候选交给 LLM
→ LLM 在候选边界内组合自然问题
```

| 概念 | 含义 |
|---|---|
| 设备能力规格 | 声明一种设备及其子部件实际存在的属性、指标和定位方式 |
| 特殊能力规格 | 声明告警、链路、子网资源和对象关系等非六类骨架能力 |
| 查询骨架 | 描述查设备信息、数量、指标或查子部件信息、数量、指标 |
| 候选能力 | 能力规格和查询骨架结合后动态生成的、可交给 LLM 使用的推荐边界 |
| 推荐问题 | LLM 根据候选能力、上下文和元数据生成的自然语言问题 |

能力卡只声明“这个对象有哪些属性和指标”。它不判断指标是否支持瞬时值、趋势、聚合、
比较或排序，这些查询形式由后续问数流程负责。

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
    "subnet": {"path": "根子网", "name": "127网段"},
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
| `question` | 保持原查询方向和自然表达 |
| `device_types` | 硬过滤设备规格；精确命中候选标准类型时额外加分 |
| `subcomponent_types` | 硬过滤子部件规格；精确命中候选标准类型时额外加分 |
| `identifiers` | 仍有效、允许继承的定位值；主候选要求定位类型与能力卡兼容 |
| `subnet` | 有效子网范围；必须由延续原对象的推荐继承，但不改变主路由或分数 |
| `properties` | 属性命中时加分；属性未命中不扣分、不屏蔽候选 |
| `kpis` | KPI 名称匹配；指定 KPI 未命中时屏蔽对应指标候选 |
| `time` | 透传给 LLM 保持用户时间方向，不参与能力过滤或打分 |
| `alarm` | 透传告警查询条件，不参与通用能力打分 |
| `aggregations` | `count` 和 `count_distinct` 用于数量骨架路由；其他值仅透传给 LLM |
| `tables` | 加载逻辑元数据，并通过 `table_hints` 为相关候选加分 |
| `recovery_strategy` | 根据共享错误码确定恢复行为 |
| `refusal_message` / `refusal_detail` | 辅助生成用户友好说明 |
| `invalid_values` | 已确认无效、禁止推荐问题继承的值 |

`Identifier` 包含：

| 字段 | 含义 |
|---|---|
| `value` | 实际定位值，例如 IP、名称或 MAC |
| `id_type` | 定位类型，例如 `IP`、`NAME`、`MAC`、`OTHER` |
| `match_mode` | 匹配方式，例如 `EXACT`、`PREFIX`、`SUFFIX`、`FUZZY`；当前不参与能力过滤和打分 |

`SubnetScope` 包含：

| 字段 | 含义 |
|---|---|
| `path` | 子网层级路径或上级范围，例如“根子网” |
| `name` | 当前子网名称，例如“127网段” |

子网范围与子网查询对象是两件事：

- `subnet={"path": "根子网", "name": "127网段"}` 表示设备或子部件查询的有效范围。
- `device_types=["子网"]` 或 `subcomponent_types=["子网"]` 表示查询对象本身是子网。
- 子网范围不会写入设备类型、子部件类型或定位条件，也不会把设备查询改路由成子网查询。
- 子网不属于网络业务域，是可包含网络、存储、服务器、PON、无线和终端对象的跨领域
  资源范围。

`build_recommendation_context(...)` 只接受共享 `query_errors.ErrorInfo`。错误分类依赖稳定
`ErrorInfo.key`，不会从拒答文案猜测类型或提取无效值。

## 六类查询骨架

设备和子部件的通用查询只使用六类骨架：

| 骨架 | 能力含义 | 主路由条件 |
|---|---|
| `device_info` | 查询设备列表、基础信息或属性 | 查信息、无子部件、无 count |
| `device_count` | 查询设备数量 | 查信息、无子部件、有 count 或 count_distinct |
| `device_metric` | 查询设备已声明的 KPI | 查指标、无子部件 |
| `subcomponent_info` | 查询设备下子部件列表、基础信息或属性 | 查信息、有子部件、无 count |
| `subcomponent_count` | 查询设备下子部件数量 | 查信息、有子部件、有 count 或 count_distinct |
| `subcomponent_metric` | 查询设备下子部件已声明的 KPI | 查指标、有子部件 |

过滤、分组、聚合、排序、TopN 和时间不单独建卡，也不由推荐能力卡判断支持范围。
告警、链路、子网资源和对象关系保留为特殊能力。

除了主骨架，算法还会生成同对象的相邻候选，帮助 LLM 给出语义不同的推荐：

| 主骨架 | 相邻候选 |
|---|---|
| `device_info` | `device_count`、`device_metric` |
| `device_count` | `device_info` |
| `device_metric` | `device_info`、`device_count` |
| `subcomponent_info` | `subcomponent_count`、`subcomponent_metric` |
| `subcomponent_count` | `subcomponent_info` |
| `subcomponent_metric` | `subcomponent_info`、`subcomponent_count` |

相邻候选会放宽定位类型校验，但仍受设备、父子对象和 KPI 名称能力边界约束。
告警、链路等特殊主路由也会按上下文是否包含子部件，补充同对象的信息和数量候选。
存在结构化 `subnet` 时，还会稳定补充兼容的 `subnet_relation` 候选，但不改变原设备
或子部件主路由。

列表和数量只作为最终推荐问题的表达形态，不拆分能力卡或新增候选类型。LLM 根据原始
`question` 自主判断：明确出现列表、有哪些、全部等表达时保持列表形态；明确出现数量、
总数、多少、几个等表达时保持数量或数量统计形态。当 `recovery_strategy` 字段不存在
或为空字符串时，三条推荐均保持明确形态，并通过过滤方向、对象范围、业务维度或分组
方向体现差异。当 `recovery_strategy` 为非空字符串时，形态仍有效则继续保留；只有失败
说明明确表明该形态或必要条件不适合继续使用时，恢复策略才优先。未明确形态时完全沿用
现有主路由、相邻候选和推荐多样性逻辑。

## 能力卡字段

内置能力卡位于 `data/device_capability_profiles.json`，包括 `device_profiles` 和
`special_capabilities`。

### 设备能力卡

```json
{
  "profile_id": "server",
  "domain": "服务器",
  "device_types": ["服务器"],
  "aliases": ["服务器设备"],
  "locators": ["IP", "NAME"],
  "properties": ["名称", "IP地址", "序列号", "型号", "厂商", "健康状态"],
  "metrics": ["CPU利用率", "内存利用率"],
  "subcomponents": [],
  "table_hints": ["服务器", "server"],
  "examples": ["查询当前服务器列表", "查询服务器数量"],
  "priority": 94
}
```

| 字段 | 是否传给 LLM | 能力含义 |
|---|---:|---|
| `profile_id` | 是，作为候选 `capability_id` 的组成部分 | 稳定且唯一的设备能力卡标识 |
| `domain` | 是 | 业务域名称，帮助 LLM 保持业务方向 |
| `device_types` | 是 | 标准设备类型；生成候选时作为允许的设备对象 |
| `aliases` | 否 | 上游设备类型的精确别名，只用于匹配能力卡，不做模糊匹配 |
| `locators` | 是 | 该设备允许继承的定位类型，例如 `IP`、`NAME` |
| `properties` | 仅信息候选 | 该设备存在的可查询属性；命中加分，未命中不屏蔽 |
| `metrics` | 仅指标候选 | 该设备存在的 KPI 标准名称；按名称忽略大小写精确匹配 |
| `subcomponents` | 间接传递 | 该设备拥有的子部件能力；也是父子对象兼容关系的唯一事实来源 |
| `table_hints` | 否 | 内部元数据相关度提示，只用于加分 |
| `examples` | 是 | 自然问法示例，只指导表达，不代表当前环境事实 |
| `priority` | 否 | 能力卡基础分，用于同类候选的稳定排序 |

### 子部件能力卡

子部件能力卡必须嵌套在所属设备能力卡内。相同子部件可以出现在多个设备能力卡中，例如
网络设备光模块和服务器光模块是两个独立能力边界。

| 字段 | 能力含义 |
|---|---|
| `types` | 子部件标准类型；候选中作为 `subcomponent_types` |
| `aliases` | 上游子部件类型的精确别名，只用于匹配，不传给 LLM |
| `properties` | 该父设备下该子部件存在的属性 |
| `metrics` | 该父设备下该子部件存在的 KPI 标准名称 |
| `table_hints` | 子部件元数据相关度提示，与父设备提示合并后参与加分 |
| `examples` | 子部件自然问法示例 |
| `priority` | 子部件附加基础分；最终基础分为设备 `priority + 子部件 priority` |

### 特殊能力卡

特殊能力不使用六类查询骨架，目前包括：

| `capability_type` | 能力含义 |
|---|---|
| `alarm_query` | 告警查询 |
| `link_query` | 网络链路和对端关系查询 |
| `resource_query` | 子网资源查询 |
| `relation_query` | 父子对象或所属关系查询 |

| 字段 | 能力含义 |
|---|---|
| `capability_id` | 特殊能力稳定标识 |
| `capability_type` | 特殊能力类型 |
| `domain` | 业务域；子网等跨领域特殊能力可以为空 |
| `device_types` | 支持的设备类型；有明确设备类型时用于硬过滤 |
| `objects` | 支持的关联对象，候选中映射为 `subcomponent_types` |
| `properties` | 特殊对象可查询的属性 |
| `table_hints` | 内部元数据相关度提示 |
| `examples` | 自然问法示例 |
| `priority` | 特殊能力基础分 |

能力卡有意不包含 `filter_fields`、`group_by_fields`、指标操作或结果形态。当前推荐模块
没有足够结构化输入来可靠约束这些内容，因此不在能力卡中提前建模。

设备类型和别名由设备能力卡统一维护，特殊能力只声明支持的标准设备类型，并通过设备
能力卡解析别名。例如 FATAP 命中网络设备能力，AP/无线接入点命中 FITAP，PON设备同时
命中 OLT 和 ONU。FC交换机属于存储领域。空意图 Basic 从原问题识别对象时优先使用最长、
最具体的对象词，避免“FC交换机”同时误命中泛化的“交换机”。

所有能力卡字段匹配均忽略英文字母大小写，包括设备类型与别名、子部件类型与别名、
定位方式、属性、指标、特殊能力对象和 `table_hints`。匹配仍是精确值或既有包含规则，
不会新增模糊匹配；候选传给 LLM 时保留能力卡中的原始标准写法。例如上游传入
`cpu利用率` 可以命中能力卡的 `CPU利用率`，候选中仍输出 `CPU利用率`。

`properties` 和 `metrics` 是两套独立能力。同一个业务词可以同时出现，例如“容量利用率”
既可以作为设备属性，也可以作为采集指标；最终使用哪一类由上游 `intention` 和六类骨架
决定。

`examples` 不是固定模板，也不参与打分。生成候选时会按骨架筛选示例：包含“数量”或
“总数”的示例用于数量候选，包含趋势、平均、Top、利用率等指标表达的示例用于指标候选，
剩余示例用于信息候选。LLM 只能学习其表达方式，不能继承示例中的具体事实。

## 候选召回与硬过滤

算法先执行确定性召回和硬过滤，再计算分数。被硬过滤的能力不会因高分重新进入候选集。

### 设备和父子对象过滤

1. `context.device_types` 非空时，只保留 `device_types` 或 `aliases` 忽略大小写精确命中的
   设备能力卡。
2. 没有设备类型但存在 `subcomponent_types` 时，只保留包含该子部件标准类型或别名的设备
   能力卡，因此多领域光模块等场景可以同时保留多个父设备领域。
3. 设备类型和子部件类型都为空时，保留全部设备能力卡。
4. 子部件候选只从其所属设备卡中生成，不允许跨父设备拼接。
5. `subnet` 只作为范围条件，不参与设备和子部件硬过滤。

### 定位方式过滤

主候选存在有效 `identifiers` 时，至少一个 `Identifier.id_type` 必须出现在能力卡
`locators` 中，否则主候选被过滤。没有定位条件时不执行定位过滤。

相邻候选用于提供低成本回退方向，会放宽定位方式过滤；这使定位值不兼容时仍可推荐
同对象列表或数量问题。

### 属性和 KPI 规则

| 输入 | 命中行为 | 未命中行为 |
|---|---|---|
| `properties` | 对信息候选加 40 分 | 不加分、不扣分、不屏蔽候选 |
| `kpis` | 对指标候选保留匹配 KPI，并加 60 分 | 指定 KPI 全部未命中时，屏蔽对应指标候选 |

KPI 按标准名称忽略大小写精确匹配。上游仍需要负责名称标准化；能力卡不维护 KPI 别名。

### 特殊能力过滤

- 特殊能力类型必须与主路由类型一致。
- 上下文和能力卡都明确设备类型时，必须存在精确交集。
- 上下文和能力卡都明确关联对象时，必须存在精确交集。
- `resource_query` 只在上下文明确出现子网对象时召回。
- `relation_query` 需要上下文对象命中，或原问题中出现对应对象词。
- 普通查信息场景只有在原问题出现“下、相连、父、子、所属”等关系表达时，才额外补充
  关系候选。
- 存在结构化 `subnet` 时，即使原问题没有关系词，也会补充兼容的 `subnet_relation`；
  设备类型与该关系能力不兼容时不会生成。

## 候选打分与排序

分数用于排列已经通过硬过滤的 `CapabilityCandidate`，不用于判断能力是否存在。当前没有
负分项。

`subnet`、`time`、`alarm`、非 count 聚合、恢复策略、拒答原因和 `invalid_values`
不直接加减分。其中恢复策略和拒答信息主要指导最终 LLM 表达，`invalid_values` 用于
禁止继承失败值。

```text
match_score =
    候选基础 priority
  + 主查询骨架匹配          160
  + 设备标准类型匹配         120
  + 子部件标准类型匹配       100
  + KPI 名称匹配              60
  + 属性名称匹配              40
  + 逻辑表或元数据提示命中   30
```

| 加分项 | 判断规则 |
|---|---|
| 基础 `priority` | 设备候选使用设备 priority；子部件候选使用设备与子部件 priority 之和；特殊能力使用自身 priority |
| 主骨架 `+160` | 候选 `capability_type` 等于 `resolve_primary_capability_type(context)` |
| 设备对象 `+120` | `context.device_types` 与候选标准 `device_types` 存在忽略大小写的精确交集 |
| 子部件对象 `+100` | `context.subcomponent_types` 与候选标准 `subcomponent_types` 存在忽略大小写的精确交集 |
| KPI `+60` | 上下文存在 KPI，且与候选 `metrics` 存在忽略大小写的精确交集 |
| 属性 `+40` | 上下文存在属性，且与候选 `properties` 存在忽略大小写的精确交集 |
| 元数据 `+30` | 任一 `table_hints` 忽略大小写后，是表名、表描述、列名或列描述拼接文本的子串 |

设备和子部件别名用于找到能力卡，但对象加分只比较候选中的标准类型。例如“服务器设备”
可以召回服务器能力卡，但不会获得标准设备类型精确匹配的 `+120`。元数据无论命中多少
个提示词都只加一次 `+30`。

示例：查询“网络设备光模块接收功率趋势”，并命中网络光模块元数据：

```text
网络设备光模块 subcomponent_metric 候选
= 网络设备 priority 95
+ 光模块 priority 10
+ 主骨架 160
+ 设备类型 120
+ 子部件类型 100
+ KPI 60
+ 元数据 30
= 575
```

排序规则依次为：

1. `match_score` 从高到低。
2. 候选 `priority` 从高到低。
3. `capability_id` 字典序，保证同分结果稳定。

排序后执行多样性裁剪。同一 `(capability_type, 第一个设备类型, 第一个子部件类型)` 分组
最多保留 2 个候选，最终默认选择 Top 12。

## 候选能力字段

`CapabilityCandidate` 是能力卡动态生成的推荐边界：

| 字段 | 含义 |
|---|---|
| `capability_id` | 动态候选稳定标识，例如 `server:网卡:subcomponent_info` |
| `capability_type` | 六类骨架或特殊能力类型 |
| `domain` | 业务域 |
| `device_types` | 候选允许的标准设备类型 |
| `subcomponent_types` | 候选允许的标准子部件或关联对象类型 |
| `locators` | 候选允许继承的定位类型 |
| `properties` | 通用信息候选或特殊能力可查询的属性 |
| `metrics` | 指标候选匹配后的 KPI 名称；非指标候选为空 |
| `table_hints` | 内部打分字段，不传给 LLM |
| `examples` | 与当前骨架匹配的表达示例 |
| `priority` | 内部排序字段，不传给 LLM |

`RankedCapability.match_score` 同样只在代码内部排序，不传给 LLM。候选数组顺序本身代表
优先级。LLM 最终收到 `capability_id`、`capability_type`、业务域、对象、定位方式、属性、
指标和示例。

## 子网范围推荐

当 `RecommendationContext.subnet` 存在时：

- 延续原设备或子部件对象的推荐必须自然继承有效子网范围。
- 同时有 `path` 和 `name` 时，推荐表达为类似“根子网下127网段的存储设备列表”。
- `name` 已经包含在完整 `path` 中时避免重复表达。
- 子网范围不参与主路由、硬过滤或打分，也不会让 `subnet_relation` 压过原对象主候选。
- 只有 `resource_query` 或 `relation_query` 可以把子网本身作为主要查询对象。
- `path` 或 `name` 位于 `invalid_values` 时，LLM 不得继续继承对应值。
- `subnet_resource` 和 `subnet_relation` 不携带固定业务域；子网关系支持网络、存储、
  服务器、PON、无线和终端对象，并保留用户明确的具体设备类型。

示例上下文：

```json
{
  "intention": "查信息",
  "question": "查询根子网下127网段的存储设备列表",
  "device_types": ["存储设备"],
  "subnet": {
    "path": "根子网",
    "name": "127网段"
  }
}
```

该上下文主路由仍为 `device_info`，会召回存储设备信息候选并补充 `subnet_relation` 候选。
相关推荐应保留“根子网下127网段”的范围，而不是退化为无范围的“查询存储设备列表”。

重要边界：

- 序列号是属性，不是设备定位方式。
- 服务器只有网卡，不提供服务器端口能力。
- 存储池、LUN、文件系统是存储设备子部件。
- 推荐模块只判断指标名称是否存在，不判断趋势、瞬时值、聚合或排序形式是否可执行。

```python
from question_recommendation import (
    load_device_capability_profiles,
    load_special_capabilities,
    recommend_capabilities,
    resolve_primary_capability_type,
)
```

可直接检查确定性召回和分数，无需调用 LLM：

```python
ranked = recommend_capabilities(context, limit=12)
for item in ranked:
    print(item.match_score, item.candidate.capability_id)
```

## 恢复策略与 Basic 兜底

恢复策略由 `refusal_rules.py` 根据共享 `ErrorInfo.key` 确定。它不会改变候选打分公式，
主要用于指导 LLM 如何组织最终推荐。

| 策略 | 推荐表达方向 |
|---|---|
| `basic` | 先定位、再收敛；优先低成本和范围更宽的问题 |
| `clarify` | 补齐对象、指标、时间或查询条件 |
| `disambiguate` | 明确业务域、父对象、设备类型或查询方向 |
| `remove_invalid` | 避开 `invalid_values`，不重新继承失败参数 |
| `reframe` | 推荐更简单、拆分后或改变查询路径的问题 |
| `adjust_scope` | 保留原方向并调整对象或时间范围 |

`recovery_strategy == "basic"` 时：

- Basic 是无法进一步细分失败类型时使用的通用兜底表达策略，不是基础能力召回模式。
- 确定性召回、能力过滤和排序与 `recovery_strategy` 字段不存在或为空字符串时完全相同。
- 当上游没有提供 `intention` 时，推荐器只从原问题中识别明确业务对象，用于收敛基础
  候选，不重建完整意图：
  - 对象词仅来自设备类型及别名、子部件类型及别名、特殊能力对象。
  - 命中设备时只召回该设备的信息和数量能力。
  - 命中子部件时只召回兼容父设备下的子部件信息和数量能力。
  - 命中告警、链路等特殊对象时只召回对应特殊能力；同时命中设备时使用设备约束它。
  - 不根据名称、状态等属性词，或拒答说明，推断对象方向。
  - 没有识别到对象时保持全局设备基础兜底。
- 完整上下文会传给 LLM；除 `invalid_values` 外，允许继承仍有效的对象、定位值、指标、
  属性、时间和业务范围。
- LLM 按“先定位，再收敛”组织推荐，优先列表、数量、基础信息、候选值和范围放宽方向，
  再结合候选能力推荐可继续收敛的原意图问题。
- 只有完全没有兼容候选时，才回退到全局设备信息和数量能力。

其他恢复策略仍由 `refusal_rules.py` 根据共享错误码确定。
明确列表或数量形态且 `recovery_strategy` 非空时，仍优先保留该形态；只有
`refusal_message` 或 `refusal_detail` 明确表明该形态或必要条件不适合继续使用时，才
允许恢复策略调整形态。

## 逻辑模型元数据

推荐器根据 `context.tables` 读取：

```text
{logical_model_path}/{table_name}.logical.yaml
```

只提取表名 `name`、表描述 `description_cn`，以及 `schema.fields` 中字段的 `name` 和
`description_cn`。元数据继续通过 `table_hints` 辅助候选排序，但不会参与能力卡召回和
硬过滤。

候选能力与实时元数据在最终 LLM 表达阶段承担不同职责：

- 候选能力决定允许推荐的业务域、设备、子部件、父子关系和查询能力方向。
- 只要至少存在一个非空 `columns[].column_description`，实时元数据就成为当前环境具体
  属性和指标的最终事实来源。
- 能力卡声明但相关实时元数据没有的属性或指标不得推荐；实时元数据存在但能力卡未声明
  的属性或指标可以推荐，但不能借此创建候选之外的对象、告警、链路或查询能力方向。
- LLM 只能使用 `column_description` 中的业务名称，不得向用户暴露 `column_name`、表名
  或物理字段名。
- 多张表存在时，只使用 `table_description` 与当前候选对象明确相关的字段；归属不明确
  的字段不得推荐。
- 存在可用实时元数据但没有适合当前对象的字段时，退化为列表、数量和基础信息等不依赖
  具体字段的问题。
- 没有加载到元数据，或所有 `column_description` 均为空时，回退使用候选能力中的
  `properties` 和 `metrics`。

例如候选能力包含服务器指标 A，而相关服务器实时元数据只包含指标 B，最终不得推荐指标
A，可以在服务器指标能力方向内推荐指标 B；设备对象和指标查询方向仍必须来自候选能力。

## 输出

```json
{
  "recommends": ["推荐问题1", "推荐问题2", "推荐问题3"],
  "explain": "说明当前提问内容、当前问题和推荐查询方向的用户友好解释"
}
```

Prompt 要求生成正好三条语义不同的问题。推荐器仍保持结构合法即原样返回，不对 LLM
返回数量做补齐、截断或拒绝；无法解析时返回 `{"recommends": [], "explain": ""}`。

`explain` 不限制字数，需要先概括用户当前查询的业务对象、查询方向和有效条件；error
场景继续说明当前问题；最后结合实际推荐问题说明建议按哪些方向继续查询。说明不得复述
`invalid_values`，也不得描述未出现在推荐结果中的能力。

未匹配场景使用委婉、非绝对的用户表达：

- 明确设备类型时逐字保留原始设备类型；存在子部件时保留父子对象关系。
- 多个设备类型时不归因于某一个类型；未明确设备时不虚构对象。
- 设备、属性、指标、枚举、关系和空结果分别表达为“暂未匹配到”“暂未采集到”或
  “暂未查询到”，不得直接表达设备不存在、字段不存在、对象没有属性/指标或不支持查询。
- `invalid_values` 仍禁止继承或复述，推荐方向必须与实际 `recommends` 一致。
- 属性或指标名称已进入 `invalid_values` 时，`explain` 使用“相关属性内容”或
  “相关指标数据”，不再点名复述；设备类型仍按原始识别结果保留。

属性或指标未匹配时，如果只有一个冲突查询项，并且相关
`metadata_tables.columns[].column_description` 中存在一个唯一、明确相似的业务描述，
LLM 可以生成一条替换推荐。该问题仅替换冲突查询项，保留原设备、子部件、定位条件、
时间、聚合和子网范围；多个冲突项、多个相似项或无明显相似项时继续使用普通基础推荐。
该规则用于优先选择与原问题相近的实时查询项，不新增相似度算法，也不得暴露物理列名、
表名或“字段”概念。

运行测试：

```bash
python3 -m pytest question_recommendation/tests query_errors/tests -q
```
