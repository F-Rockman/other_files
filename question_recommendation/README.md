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

## 能力召回模块结构

`capabilities.py` 是稳定公共入口，仅负责编排卡片加载、候选召回、排序和裁剪。内部实现
按职责拆分，避免召回规则继续堆积在单个文件：

| 模块 | 职责 |
|---|---|
| `capability_loader.py` | 一次读取并解析领域卡和特殊卡 |
| `capability_routing.py` | 根据意图、子部件和数量聚合确定主查询骨架 |
| `capability_matching.py` | 对象、文本、定位方式和通用值匹配 |
| `capability_candidates.py` | 设备、子部件、特殊能力和相邻候选生成 |
| `capability_recall.py` | 空意图 Basic、拒答方向和常规召回编排 |
| `capability_ranking.py` | 候选评分、稳定排序和多样性裁剪 |

外部调用继续只使用 `question_recommendation.capabilities` 或包级导出，不依赖内部模块。
测试限制每个能力模块不超过 500 行、模块级函数不超过 50 行且圈复杂度不超过 8。

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
| `devices` | 逐项保存设备定位条件及其原始设备类型；运行时派生设备类型和有效定位方式 |
| `subcomponent_types` | 硬过滤子部件规格；精确命中候选标准类型时额外加分 |
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

`DeviceCondition` 与上游 `devices[]` 同构：

| 字段 | 含义 |
|---|---|
| `device_id` | 实际定位值，例如 IP、名称或 MAC；不会改写为 `value` |
| `id_type` | 定位类型，例如 `IP`、`NAME`、`MAC`、`OTHER` |
| `match_mode` | 匹配方式，例如 `EXACT`、`PREFIX`、`SUFFIX`、`FUZZY`；当前不参与能力过滤和打分 |
| `device_type` | 该定位条件对应的原始设备类型，始终与本条定位值绑定 |

设备类型不再单独存储。推荐器在召回时从所有非空 `devices[].device_type` 实时去重派生，
定位方式只从仍有非空 `device_id` 的设备条件派生。设备定位值失效时，会清空该条件的
`device_id / id_type / match_mode`，但保留 `device_type`，使推荐仍能围绕已识别类型生成。
这是破坏性结构升级：`RecommendationContext.from_dict()` 不再读取旧 `identifiers`、
顶层 `device_types` 或 `value` 字段。

`SubnetScope` 包含：

| 字段 | 含义 |
|---|---|
| `path` | 子网层级路径或上级范围，例如“根子网” |
| `name` | 当前子网名称，例如“127网段” |

子网范围与子网查询对象是两件事：

- `subnet={"path": "根子网", "name": "127网段"}` 表示设备或子部件查询的有效范围。
- `devices=[{"device_type": "子网"}]` 或 `subcomponent_types=["子网"]` 表示查询对象本身是子网。
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
方向体现差异。`simplify` 始终优先于形态继承；其他 `recovery_strategy` 非空时，形态
仍有效则继续保留，只有失败说明明确表明该形态或必要条件不适合继续使用时才调整形态。
未明确形态时完全沿用现有主路由、相邻候选和推荐多样性逻辑。

所有场景都禁止推荐与原问题语义完全一致的问题，也禁止仅在列表和数量之间切换。推荐
不能只换词、调整语序，或为了制造差异追加原问题中不存在的过滤值、范围和业务条件。
该约束由 Prompt 和 LLM 自检执行，推荐器不增加结果内容后处理。

### 多定位备选条件

`devices[]` 保留每个定位条件与设备类型的对应关系。非链路查询处于 `disambiguate`
恢复策略、至少有两个有效设备定位条件，且原问题明确使用“或”“或者”或独立英文 `OR`
表达备选关系时，LLM 会将条件拆成独立推荐：

```text
查询 IP 为 A 或名称包含 B 的网络设备
→ 查询 IP 为 A 的网络设备列表
→ 查询名称包含 B 的网络设备列表
→ 查询 IP 为 A 的网络设备数量
```

每条推荐最多继承一个完整 `DeviceCondition`，不得重新组合不同条件。原问题明确列表或
数量时继续保持对应形态。`intention == "查链路"` 时永远不执行该拆分；`link_relation`
属于链路语义，不进入 `RecommendationContext`，也不用于判断普通多设备条件是否为
备选关系。

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
| `objects` | 支持的特殊对象，候选中作为 `objects` 传给 LLM，不写入 `subcomponent_types` |
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
特殊能力场景下，原问题中的已知设备词会先尝试按设备能力卡归一；推荐模块不从文本中
抽取未知设备实体。最终推荐只能使用结构化设备或候选 `device_types` 中的设备表达。

所有能力卡字段匹配均忽略英文字母大小写，包括设备类型与别名、子部件类型与别名、
定位方式、属性、指标、特殊能力对象和 `table_hints`。匹配仍是精确值或既有包含规则，
不会新增模糊匹配；候选传给 LLM 时保留能力卡中的原始标准写法。例如上游传入
`cpu利用率` 可以命中能力卡的 `CPU利用率`，候选中仍输出 `CPU利用率`。

`properties` 和 `metrics` 是两套独立能力。同一个业务词可以同时出现，例如“容量利用率”
既可以作为设备属性，也可以作为采集指标；最终使用哪一类由上游 `intention` 和六类骨架
决定。

`examples` 不是固定模板，也不参与打分。生成候选时会按骨架筛选示例：包含“数量”或
“总数”的示例用于数量候选；只有命中当前设备或子部件能力卡 `metrics` 的示例才用于
指标候选；剩余示例用于信息候选。LLM 只能学习其表达方式，不能继承示例中的具体事实。

## 候选召回与硬过滤

算法先执行确定性召回和硬过滤，再计算分数。被硬过滤的能力不会因高分重新进入候选集。

### 设备和父子对象过滤

1. 从 `context.devices[].device_type` 派生的设备类型非空时，只保留 `device_types` 或
   `aliases` 忽略大小写精确命中的设备能力卡。
2. 没有设备类型但存在 `subcomponent_types` 时，只保留包含该子部件标准类型或别名的设备
   能力卡，因此多领域光模块等场景可以同时保留多个父设备领域。
3. 设备类型和子部件类型都为空时，保留全部设备能力卡。
4. 子部件候选只从其所属设备卡中生成，不允许跨父设备拼接。
5. `subnet` 只作为范围条件，不参与设备和子部件硬过滤。

### 定位方式过滤

主候选存在 `device_id` 非空的设备条件时，至少一个对应 `DeviceCondition.id_type` 必须
出现在能力卡 `locators` 中，否则主候选被过滤。没有有效定位值时不执行定位过滤。

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
| 设备对象 `+120` | 从 `context.devices[].device_type` 派生的类型与候选标准 `device_types` 存在忽略大小写的精确交集 |
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
| `subcomponent_types` | 候选允许的标准子部件类型 |
| `objects` | 特殊能力的对象，例如告警、链路、子网或关联对象 |
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
  "devices": [{"device_type": "存储设备"}],
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
    load_capability_cards,
    recommend_capabilities,
    resolve_primary_capability_type,
)
```

`load_capability_cards()` 只读取一次内置配置，并同时返回领域卡和特殊卡：

```python
domain_cards, special_cards = load_capability_cards()
```

也可以显式注入卡片，避免推荐调用读取内置配置：

```python
ranked = recommend_capabilities(
    context,
    domain_cards=domain_cards,
    special_cards=special_cards,
    limit=12,
)
```

两类卡片都传入时不会读取内置配置；任一类为空时，会统一读取一次配置并仅补齐缺失卡片。
空序列表示使用内置卡片，不能用于明确禁用某类能力卡。

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
| `simplify` | 删除原问题中的复杂条件，保留对象和核心查询目标 |
| `adjust_scope` | 保留原方向并调整对象或时间范围 |

`sql_generation_failed` 和 `query_execution_engine_error` 使用 `simplify`。原先需要改写
问题方向的其他错误统一使用 `basic`；`sql_generation_timeout` 继续使用
`adjust_scope`。

拒答恢复场景下，推荐问题不得继承或生成未来时间，例如明天、后天、下周、下月、明年、
未来某天，或明确晚于当前日期的绝对时间。原问题包含未来时间时，LLM 优先删除该时间
条件，或仅继承上下文中明确非未来的时间范围。

### Simplify 简化查询

`simplify` 优先于空 `intention` Basic、参数继承、子网继承和列表/数量形态继承：

- 默认保留原业务对象、父子关系和至少一个 KPI、属性、告警或关系等核心查询目标。
- 可以删除时间、子网、设备定位值、聚合、分组、排序、TopN、过滤条件、额外设备条件，
  以及多 KPI、多属性或多目标中的额外目标。
- 三条推荐优先分别删除不同的单一条件；不足三种时，再组合删除多个条件。
- 每条推荐必须至少删除一个原条件，不能保留全部条件，也不能追加新条件。
- 原问题没有可删除条件时，改为推荐同对象的其他指标、属性或基础能力。
- `explain` 只向用户说明查询条件较复杂以及减少条件的方向，不暴露查询语句、查询引擎
  或内部错误。

例如：

```text
查询近一小时根子网下IP为A的网络设备CPU利用率平均值
→ 查询根子网下IP为A的网络设备CPU利用率平均值
→ 查询近一小时IP为A的网络设备CPU利用率平均值
→ 查询近一小时根子网下IP为A的网络设备CPU利用率
```

### 拒答场景的问题方向收敛

当 `recovery_strategy` 非空，且结构化上下文没有非空 `devices[].device_type` 和
`subcomponent_types` 时，推荐器会从原始 `question` 中补充业务方向：

- 匹配词只来自能力卡已有的 `domain`、设备类型与别名、子部件类型与别名，匹配时忽略
  英文字母大小写，不读取属性、指标、时间或拒答文案。
- 命中领域词时只保留该领域设备能力。例如“查询网络CUP”只保留网络领域候选；
  “查询PON CUP”保留 OLT 和 ONU；多个领域词同时出现时保留全部命中领域。
- 命中具体设备或子部件时使用更具体的对象方向；同时命中领域与子部件时，只保留该领域
  内兼容的父子对象。
- 已有结构化设备或子部件时不启用文本方向补充，避免原问题中的其他词覆盖识别结果。
- 没有命中能力卡已有方向时，保持原有全局召回逻辑。

对于 `clarify` 或 `disambiguate` 的指标查询，如果原 KPI 在命中方向内无法生成任何指标
候选，推荐器会忽略该错误 KPI，并补回该方向能力卡中的标准指标候选。错误 KPI 不会进入
候选，也不会因此召回其他领域。例如：

```text
question = 查询网络CUP
intention = 查指标
kpis = ["CUP"]
recovery_strategy = disambiguate

候选 = 网络设备指标 + 网络设备信息 + 网络设备数量
```

告警等特殊能力也使用补充方向约束设备范围。例如“网络告警”只生成网络设备范围的告警
候选。最终 LLM 必须严格围绕已收敛候选表达，不得重新扩展到其他业务域。

### 空 `intention` 直接复用 Basic

当 `RecommendationContext.intention` 为空且 `recovery_strategy` 不是 `simplify` 时，
推荐器直接进入扩展后的 Basic 流程。`simplify` 即使在空 `intention` 下仍优先执行：

- 有结构化 `devices[].device_type` 或 `subcomponent_types` 时，优先按结构化对象收敛；
  不使用原问题中的其他对象词覆盖识别结果。
- 没有结构化对象时，从原始 `question` 中匹配能力卡已有的设备、子部件和特殊对象。
- 命中设备方向时生成设备信息、数量和指标候选；命中子部件方向时生成子部件信息、数量
  和指标候选。没有指标定义的对象不会生成空指标候选。
- 命中告警、链路等特殊对象时，仍只召回相应特殊能力。
- 无法识别对象方向时，对全部领域卡生成设备信息、数量和指标候选，再沿用现有评分、
  多样性裁剪和 Top 12 选择。
- 生成指标候选时不使用上下文中可能不完整或未标准化的 KPI 屏蔽候选；候选只证明对象
  存在指标查询方向。

LLM 优先继承上下文中的结构化设备、子部件、KPI、时间、聚合和范围。上下文缺少相应
内容时，可以从原问题中受控继承明确出现的设备表达、KPI、时间、聚合、排序和 TopN，
但不得虚构值、继承 `invalid_values` 或突破候选的对象、父子关系和特殊能力边界。

空 `intention` 是实时元数据字段优先级的受控例外：原问题中明确出现的 KPI 即使没有
出现在候选 `metrics` 或实时元数据中，也可以继续用于推荐，但候选必须存在对应对象层级
的 `device_metric` 或 `subcomponent_metric` 方向。该例外只放宽 KPI 名称，不扩展对象。

当拒答原因明确表示多意图时，LLM 优先把原问题拆成独立可执行问题：先覆盖不同 KPI，
再拆分同一 KPI 的不同聚合，并按原问题顺序选择，最终始终输出三条，不能把已拆开的
意图重新组合。

### 非空 `intention` 的 Basic

`intention` 非空且 `recovery_strategy == "basic"` 时：

- Basic 是无法进一步细分失败类型时使用的通用兜底表达策略，不是基础能力召回模式。
- 确定性召回、能力过滤和排序与 `recovery_strategy` 字段不存在或为空字符串时完全相同。
- 完整上下文会传给 LLM；除 `invalid_values` 和未来时间外，允许继承仍有效的对象、
  定位值、指标、属性、时间和业务范围。
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
- 每条推荐必须绑定一张具体候选能力卡。设备类型、子部件关系、属性或指标和查询能力
  必须同时由该候选支持，不能把多张候选卡的字段并集当作通用白名单。
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
- 没有加载到元数据，或所有 `column_description` 均为空时，相关候选的 `properties`
  和 `metrics` 成为最终字段白名单。未命中的原属性或指标不得继续进入推荐，也不得从
  原问题重新继承。
- 调用 LLM 前会将原属性和 KPI 与最终候选字段做忽略大小写的精确比较。当某个查询项
  未被任何最终候选支持时，通过内部 `candidate_field_analysis` 明确标记为全局未命中；
  任意候选支持该项时则继续使用单卡绑定规则。
- 全局未命中时，每条推荐优先从当前绑定候选自身字段中选择语义相近项；替换后不得继承
  原字段绑定的过滤值。例如不能将“运行状态正常”直接改成“状态正常”。
- 没有清晰相近字段时，才剔除冲突字段及其关联取值，继承其他有效对象、父子关系、定位
  条件、时间、子网范围和明确结果形态；最后才回退到同对象基础信息。
- 空 `intention` 时允许受控继承原问题中明确出现的 KPI，但必须存在当前对象层级的指标
  查询候选；非空 `intention` 不使用该例外。

例如候选能力包含服务器指标 A，而相关服务器实时元数据只包含指标 B，最终不得推荐指标
A，可以在服务器指标能力方向内推荐指标 B；设备对象和指标查询方向仍必须来自候选能力。

## Prompt 动态组装

运行时 system Prompt 由三部分组成：

```text
固定核心规则
+ 按结构化上下文精确选择的场景片段
+ 固定输出与自检规则
```

`QUESTION_RECOMMENDATION_SYSTEM_PROMPT` 保留为可独立使用的核心 Prompt 与输出规则组合；
`QUESTION_RECOMMENDATION_PROMPT` 继续用于兼容既有常量导入。Chat 推荐接口会在核心规则和
输出规则之间插入当前请求需要的场景片段。

动态片段只根据结构化字段和已加载元数据选择，不读取问题或拒答文案关键词：

| 精确条件 | 动态片段 |
|---|---|
| `recovery_strategy == "simplify"` | 查询简化规则 |
| `intention` 为空且不是 `simplify` | 空 intention Basic |
| `intention` 非空且有已知恢复策略 | 对应恢复策略 |
| `intention` 非空且没有恢复策略 | 无恢复要求的弱推荐路径 |
| 拒答恢复且没有结构化设备或子部件 | 拒答业务方向约束 |
| 存在有效 `subnet` | 子网范围规则 |
| 至少一个 `column_description` 非空 | 实时元数据最终字段规则 |
| 没有任何非空 `column_description` | 能力卡字段白名单与回退规则 |

多意图、明确缺失属性、列表或数量表达、多定位“或”关系和未匹配类型等必须理解文本才能
判断的规则，压缩后保留在核心 Prompt，不参与动态片段选择。这样不会因为拒答文案措辞
变化而错误加载规则，也避免每次请求都发送所有恢复场景说明。

### Prompt 规则契约

下表记录高影响规则的稳定归属。修改或压缩 Prompt 时，应同步保留对应语义和测试；测试
名称用于定位契约，不要求 Prompt 保留固定长文案。

| 规则 ID | 所属片段 | 加载条件 | 契约测试 |
|---|---|---|---|
| `CORE-CAPABILITY-BOUNDARY` | 核心 | 始终 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-SINGLE-CANDIDATE-BINDING` | 核心 | 始终 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-LOCATOR-BOUNDARY` | 核心 | 始终 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-RESULT-FORM` | 核心 | 始终，由 LLM 理解原问题形态 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-MISSING-PROPERTY` | 核心 | 始终，由 LLM 理解拒答原因 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-MULTI-INTENT` | 核心 | 始终，由 LLM 理解拒答原因 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `CORE-ALTERNATIVE-DEVICE` | 核心 | 始终，由 LLM 理解原问题“或”关系 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `NORMAL-WEAK-PATH` | 无恢复要求 | `intention` 非空且 `recovery_strategy` 为空 | `test_normal_runtime_prompt_loads_weak_path_and_is_clearly_shorter` |
| `RECOVERY-SIMPLIFY` | simplify | `recovery_strategy == "simplify"` | `test_simplify_fragment_overrides_empty_intention_basic` |
| `RECOVERY-BASIC` | Basic | 空 intention 或 `recovery_strategy == "basic"` | `test_empty_intention_uses_basic_fragment_for_other_strategies`、`test_nonempty_intention_selects_only_matching_recovery_fragment` |
| `RECOVERY-DIRECTION` | 拒答业务方向 | 有恢复策略且没有结构化设备或子部件 | `test_recovery_direction_fragment_requires_recovery_and_no_structured_object` |
| `SCOPE-SUBNET` | 子网范围 | 存在有效 `subnet` | `test_subnet_fragment_is_selected_only_by_structured_subnet` |
| `METADATA-FIELD-SOURCE` | 实时元数据 | 存在非空 `column_description` | `test_metadata_fragment_requires_nonempty_column_description` |
| `CAPABILITY-FIELD-SOURCE` | 无实时元数据 | 没有非空 `column_description` | `test_no_metadata_fragment_uses_candidate_fields_as_strict_whitelist` |
| `CAPABILITY-GLOBAL-MISS` | 无实时元数据 | 原字段未被任何最终候选精确支持 | `test_candidate_field_analysis_marks_field_missing_from_all_final_candidates` |
| `OUTPUT-JSON` | 输出与自检 | 始终 | `test_core_prompt_keeps_global_and_text_interpretation_rules` |
| `ROUTING-NO-TEXT-KEYWORDS` | 动态构建器 | 始终 | `test_question_and_refusal_text_do_not_select_dynamic_fragments` |

关键细则：

- 只有候选 `locators` 支持的定位类型可以继承；不支持的定位值不得进入推荐问题。
- 原问题没有明确列表或数量时，不主动推断或强制选择这两种形态。
- 无恢复要求时，只把信息、数量、指标和关联能力作为弱路径，不主动虚构趋势、聚合、
  排序或 TopN。
- 子网 `path/name` 逐字继承；`name` 已包含于完整 `path` 时避免重复。
- 唯一相似元数据替换必须保留设备类型、父子关系、定位条件、时间、聚合和子网范围，
  并在 `explain` 中自然引导用户尝试相近查询内容。
- 无实时元数据时，每条推荐绑定一张候选卡，该卡字段是当前推荐的最终白名单。原字段
  精确命中当前卡时可继承过滤值；未命中时只可使用当前卡的相近字段且不得继承原过滤值。
- 原字段未被任何最终候选精确支持时，优先使用各绑定候选自己的相近字段；没有相近字段
  时才删除冲突字段及其关联取值，并保留其他有效查询条件。
- 跨领域推荐不能交叉拼接对象和字段。例如服务器卡包含“运行状态”时可以保留“正常”，
  网络设备卡只有“状态”时只能查看状态，不能生成“运行状态正常的网络设备”。

## 输出

```json
{
  "recommends": ["推荐问题1", "推荐问题2"],
  "explain": "说明当前提问内容、当前问题和推荐查询方向的用户友好解释"
}
```

Prompt 要求生成 1 到 3 条语义不同的问题；候选不足或质量低时不强行凑满。推荐器仍保持
结构合法即原样返回，不对 LLM 返回数量做补齐、截断或拒绝；无法解析时返回
`{"recommends": [], "explain": ""}`。

`explain` 不限制字数，但应清晰、自然，并完整包含三部分：先概括用户当前想查询的业务
对象、查询方向和仍有效条件；再用用户能够理解的方式说明当前查询为什么不适合直接继续；
最后结合实际推荐问题，说明接下来可以按哪些方向查询。可以概括原问题，但不要逐字照抄；
可以解释业务原因，但不要机械复述拒答文案或写成系统处理日志。

禁止使用“错误原因是”“失败原因是”“推荐调整为”“建议调整为”“推荐方向是”
“基于上述原因”“针对该错误”“系统建议”“支持查看”“可查看”等报告式、能力说明式
或流程式表达。优先自然说明不同对象的信息差异，并使用“可以先……”“可以分别……”
“先确认……后再……”等行动表达，但不要机械复用同一句式。推荐方向必须与实际问题一致，
不能描述未推荐的能力，也不能解释系统为什么选择这些推荐。

不得责备用户，也不得使用带有指责、纠正或质疑用户表达能力的措辞。

理由中提到的对象名称必须沿用实际推荐问题，不得泛化。例如推荐使用“闪存存储”时，
`explain` 也必须使用“闪存存储”，不能改写成“存储设备”。

无实时元数据且原属性或指标未被任何最终候选支持时，理由必须将不支持内容归属到实际
推荐使用的具体设备类型或父子对象。例如表达为“闪存存储不支持节点信息属性查询”，
不能笼统表达为“现有数据中暂未匹配到节点信息”，也不能暴露字段、元数据或映射过程。

未查询到与不支持场景使用用户可理解的明确表达：

- 明确设备类型时逐字保留原始设备类型；存在子部件时保留父子对象关系。
- 多个设备类型时不归因于某一个类型；未明确设备时不虚构对象。
- 设备定位未查询到时按 `id_type + match_mode` 自然表达，例如“当前未查询到
  IP地址以 A 开头的设备”；`OTHER` 优先沿用原问题中的定位词，无法确定时表达为
  “当前未查询到与 A 匹配的设备”。
- 属性或指标不被候选支持时，明确表达为“设备类型A不支持属性1属性查询”或
  “设备类型A不支持指标1指标查询”，但不得暴露字段、元数据或映射过程。
- 聚合、趋势、TopN、特殊能力或其他查询方向不被候选支持时，也使用带对象归属的
  “对象 + 不支持 + 能力/查询方向”表达；无法安全确定对象时，不虚构对象，先引导用户
  明确具体对象或查询方向。
- 关系未查询到时表达为“不存在设备A到设备B的关联关系”；取值不存在时表达为
  “设备类型A‘属性1’不存在‘取值A’这一取值”。
- `invalid_values` 仍禁止进入推荐问题；仅设备定位未查询到场景可在 `explain`
  原因中说明失败定位值，推荐方向必须与实际 `recommends` 一致。
- 属性或指标名称已进入 `invalid_values` 时，`explain` 使用“相关属性内容”或
  “相关指标数据”，不再点名复述；设备类型仍按原始识别结果保留。

当 `refusal_message` 或 `refusal_detail` 明确点名某个属性不存在、缺少对应字段或无法
提供时，LLM 会在最终表达阶段剔除该属性，即使它没有进入 `invalid_values`。剔除规则
优先于原问题参数继承、列表/数量形态、Basic 兜底和推荐多样性；不得将该属性改写为
子部件、列表、数量或统计问题。例如“缺少节点字段”时，不得继续推荐节点信息、节点
列表或节点数量。设备定位条件、设备类型、时间和子网等其他有效条件继续保留。

只有失败原因明确点名具体属性时才执行该规则；“字段检索失败”“缺少字段”或“未找到
匹配字段”等泛化原因不会主动删除原问题内容。存在唯一明确相似元数据时，可生成一条
替换推荐；否则回退到候选范围内不依赖该属性的基础方向。

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
