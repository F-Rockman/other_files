# question_recommendation

基于“结构化模板定义能力边界，LLM 负责自然表达”的问数推荐模块。

推荐输入的优先级为：

```text
recognized_intent > StructuredTemplate 标签 > 失败原因 > metadata_tables > 模板原文
```

## 调用接口

```python
recommend_questions_chat(
    user_question,
    llm_chat_client,
    scene_type="error",
    intercept_reason="",
    intercept_detail="",
    recognized_intent=None,
    candidate_templates=None,
    logical_model_path_provider=None,
    business_info=None,
)
```

| 参数 | 必填 | 含义 | 缺失影响 |
|---|---|---|---|
| `user_question` | 是 | 用户原始问题 | 无法保持原问题表达和具体条件 |
| `llm_chat_client` | 是 | 接收 messages 并返回字符串的 Chat LLM 调用函数 | 无法生成推荐 |
| `scene_type` | 否 | `error` 或 `normal`，默认 `error` | 默认按失败恢复处理 |
| `intercept_reason` | error 场景建议 | 对用户可理解的失败原因 | 无法准确判断恢复策略和异常参数 |
| `intercept_detail` | 否 | 失败补充信息 | 复杂失败场景恢复准确率下降 |
| `recognized_intent` | 强烈建议 | 前一步结构化意图识别结果 | 更依赖模板文本，业务跑偏风险上升 |
| `candidate_templates` | 强烈建议 | 外部召回的 Top N 结构化模板 | 只能尝试基于意图做通用兜底；意图也为空时返回空推荐 |
| `logical_model_path_provider` | 有 `tables` 时建议 | 返回所有 `.logical.yaml` 文件所在目录的方法 | 不读取表列元数据，仍可依靠意图和模板推荐 |
| `business_info` | 否 | 额外业务说明或约束 | 不影响核心流程 |

## RecognizedIntent

前一步意图识别结果。字段值可以是字符串、列表或字典；建议使用字典保留更多结构化信息。

| 字段 | 建议级别 | 含义 | 示例 |
|---|---|---|---|
| `intent_type` | 必填 | 核心查询意图 | `查信息`、`查告警`、`查指标`、`查链路` |
| `domain_info` | 多域对象必填 | 原始业务域 | `网络`、`服务器`、`存储`、`PON` |
| `subnet_info` | 涉及时填写 | 子网对象、条件及匹配方式 | `{"name": "核心网子网"}` |
| `device_info` | 涉及时填写 | 设备类型、定位条件、匹配方式和查询范围 | `{"object": "网络设备", "ip_prefix": "10.1", "match_type": "prefix"}` |
| `sub_component_info` | 涉及时填写 | 设备子部件 | `{"object": "接口"}` |
| `attribute_info` | 涉及时填写 | 查询属性 | `{"attribute": "健康状态"}` |
| `metric_info` | 查指标建议必填 | 性能指标 | `{"metric": "CPU利用率"}` |
| `time_info` | 涉及时填写 | 时间范围和时间粒度 | `{"range": "最近24小时", "grain": "小时"}` |
| `alarm_info` | 查告警建议必填 | 告警名称、级别、状态等 | `{"status": "未恢复"}` |
| `aggregation_operator` | 涉及时填写 | 聚合或展示算子 | `平均值`、`最大值`、`数量`、`TopN` |
| `tables` | 有关联表时填写 | 本次意图关联到的全部逻辑表名 | `["network_device", "network_device_metric"]` |
| `extra` | 否 | 暂未标准化的扩展信息 | `{"matched_device_count": 12}` |

推荐保留匹配语义，而不只传最终值。例如 IP 前缀条件建议传成：

```python
RecognizedIntent(
    intent_type="查指标",
    domain_info="网络",
    device_info={
        "object": "网络设备",
        "ip_prefix": "10.1",
        "match_type": "prefix",
        "matched_scope": "multiple",
    },
    metric_info={"metric": "CPU利用率"},
    aggregation_operator="平均值",
    tables=["network_device", "network_device_metric"],
)
```

## StructuredTemplate

结构化模板是推荐能力单元。标签决定“能不能推荐”，`template_text` 决定“如何表达”。

| 字段 | 建议级别 | 含义 | 不填写的影响 |
|---|---|---|---|
| `template_id` | 必填 | 模板稳定唯一标识 | 不影响运行，但难以维护、评估和排障 |
| `template_text` | 必填 | 可自然化改写的问题骨架 | 无法基于该模板生成问题 |
| `intent_tags` | 必填 | 支持的用户意图 | 意图排序准确率下降 |
| `domain_tags` | 多域对象必填 | 适用业务域 | 无法依靠该字段阻止跨域推荐 |
| `object_tags` | 必填 | 模板涉及对象，建议父对象在前 | 对象跑偏风险明显上升 |
| `parent_object` | 子部件模板建议必填 | 父对象 | 父对象失败后的恢复能力下降 |
| `child_object` | 子部件模板建议必填 | 子对象 | 可能退化成父对象级推荐 |
| `template_type` | 必填 | 列表、数量、基础信息、指标、趋势、告警、链路等 | 兜底排序和表达生成不稳定 |
| `slots` | 有槽位时必填 | 需要继承或让用户补充的参数 | 定位条件可能无法正确自然化 |
| `supported_recovery_types` | error 模板建议必填 | 适用失败恢复类型 | 失败场景只能靠业务域和对象排序 |
| `priority` | 否 | 静态优先级，值越大越优先 | 默认按 0 处理 |
| `extra` | 否 | 暂未标准化的模板标签 | 不影响标准流程 |

多业务域对象包括接口、端口、光模块、硬盘、风扇、电源、机框等。此类模板必须填写
`domain_tags`，否则容易跨域推荐。

```python
StructuredTemplate(
    template_id="network_device_avg_cpu_by_ip",
    template_text="查询 IP 为“IP地址”的网络设备平均 CPU 利用率",
    intent_tags=["查指标"],
    domain_tags=["网络"],
    object_tags=["网络设备"],
    template_type="指标",
    slots=["device_ip"],
    supported_recovery_types=["匹配到多设备", "对象定位失败"],
    priority=80,
)
```

## 逻辑模型元数据

调用方不直接传表列信息。推荐器从 `recognized_intent.tables` 获取所有表名，并调用
`logical_model_path_provider` 获取存放逻辑模型文件的目录。

```python
def logical_model_path_provider() -> str:
    return "/data/logical-models"

result = recommend_questions_chat(
    ...,
    recognized_intent=intent,
    logical_model_path_provider=logical_model_path_provider,
)
```

对于表名 `network_device`，推荐器读取：

```text
/data/logical-models/network_device.logical.yaml
```

逻辑模型文件结构：

```yaml
name: network_device
description_cn: 网络设备
schema:
  fields:
    - name: device_ip
      description_cn: 设备IP地址
    - name: device_name
      description_cn: 设备名称
```

推荐器只提取：

| YAML 路径 | 用途 |
|---|---|
| `name` | 表名 |
| `description_cn` | 表描述 |
| `schema.fields[].name` | 列名 |
| `schema.fields[].description_cn` | 列描述 |

推荐器会在 Prompt 中自动按表组织：

```json
[
  {
    "table_name": "network_device",
    "table_description": "网络设备",
    "columns": [
      {"column_name": "device_ip", "column_description": "设备IP地址"}
    ]
  },
  {
    "table_name": "network_device_metric",
    "table_description": "网络设备性能指标",
    "columns": [
      {"column_name": "avg_cpu_usage", "column_description": "平均CPU利用率"}
    ]
  }
]
```

没有表列信息时，只要 `recognized_intent` 和 `candidate_templates` 足够完整，仍然可以正常推荐。
但模块不会根据未知字段自由扩展问题。

单个逻辑模型文件缺失或格式异常时会跳过该表，不阻断推荐。路径提供方法返回无效目录，
或环境未安装 PyYAML 时，会抛出 `LogicalMetadataError`。

安装依赖：

```bash
pip install -r question_recommendation/requirements.txt
```

## 多设备失败示例

用户问题：

```text
查询 IP 以 10.1 开头的设备平均 CPU 利用率
```

前一步识别出查指标、IP 前缀、CPU 利用率、平均值，并返回“匹配到多设备”错误时：

- IP 前缀属于有效查询范围，不应作为异常参数删除。
- 如果当前能力只支持单设备指标查询，应先推荐设备列表、设备数量和单设备指标查询。
- 若模板明确支持按设备分组聚合，可以推荐“查询 IP 以 10.1 开头的各设备平均 CPU 利用率”。

建议候选模板至少覆盖：

```python
[
    StructuredTemplate(
        template_id="network_device_list_by_ip_prefix",
        template_text="查询 IP 以“IP前缀”开头的网络设备列表",
        intent_tags=["查信息"],
        domain_tags=["网络"],
        object_tags=["网络设备"],
        template_type="列表",
        slots=["ip_prefix"],
        supported_recovery_types=["匹配到多设备"],
    ),
    StructuredTemplate(
        template_id="network_device_count_by_ip_prefix",
        template_text="查询 IP 以“IP前缀”开头的网络设备数量",
        intent_tags=["查信息"],
        domain_tags=["网络"],
        object_tags=["网络设备"],
        template_type="数量",
        slots=["ip_prefix"],
        supported_recovery_types=["匹配到多设备"],
    ),
    StructuredTemplate(
        template_id="network_device_avg_cpu_by_ip",
        template_text="查询 IP 为“IP地址”的网络设备平均 CPU 利用率",
        intent_tags=["查指标"],
        domain_tags=["网络"],
        object_tags=["网络设备"],
        template_type="指标",
        slots=["device_ip"],
        supported_recovery_types=["匹配到多设备"],
    ),
]
```

## 输出

```json
{
  "recommends": [
    "推荐问题1",
    "推荐问题2",
    "推荐问题3"
  ],
  "explain": "80字以内的推荐理由"
}
```

LLM 输出非法 JSON、推荐不足三条、包含异常参数或粗枚举表达时，调用器会优先使用同域、
同对象的基础模板补足。无法确定业务域或对象时，不会强行生成具体业务问题。
