# 单位配置速查表

> 本文档汇总 `unit_definitions` 目录中的 41 个单位类型、115 条单位定义。

## 字段说明

- `is_standard: Y` 表示该 `unit_type` 的换算基准单位，不表示只有该项属于 SI 或正式标准。
- 当前共有 41 条基准定义（`Y`）和 74 条非基准定义（`N`）；`N` 不表示单位不规范。
- `standard_conversion_rule` 表示当前单位换算到同类型基准单位的规则。纯数字表示 `基准值 = 当前值 × 系数`；含 `value` 的内容表示换算表达式。
- 查询单位时应同时使用 `unit_type`。建议使用 `(unit_type, name)` 或 `(unit_type, unit_symbol)`，不能假设名称或符号全局唯一。
- `data_size`、`byte_rate` 默认采用十进制前缀；二进制前缀使用 `binary_data_size`、`binary_byte_rate`。它们属于不同换算域，跨类型换算应通过共同的 `B` 或 `B/s` 基准衔接。
- 单位符号严格区分大小写和 Unicode 字符，例如 `B`、`bit`、`kB`、`MB`、`μ`、`s⁻¹`、`A·h`。
- `ratio` 的基准符号 `!%` 是项目自定义的无量纲单位“一”标记，不是正式计量符号；普通 `%` 通过系数 `0.01` 换算到该基准。
- 为保持表格便于速查，完整清单未重复展示 `description` 和 `description_cn`；可通过每行的配置文件链接查看完整定义。

## 单位类型索引

| 单位类型 | 中文类型 | 基准符号 | 定义数 | 配置文件 |
|---|---|---:|---:|---|
| `angular_velocity` | 角速度 | `rad/s` | 2 | [angular_velocity.unit.yaml](angular_velocity.unit.yaml) |
| `apparent_power` | 视在功率 | `VA` | 2 | [apparent_power.unit.yaml](apparent_power.unit.yaml) |
| `binary_byte_rate` | 二进制前缀字节率 | `B/s` | 5 | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| `binary_data_size` | 二进制前缀数据量 | `B` | 6 | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| `bit_rate` | 比特率 | `bit/s` | 5 | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| `byte_rate` | 字节率 | `B/s` | 5 | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| `connection_rate` | 连接建立速率 | `s⁻¹` | 1 | [connection_rate.unit.yaml](connection_rate.unit.yaml) |
| `data_size` | 数据量 | `B` | 8 | [data_size.unit.yaml](data_size.unit.yaml) |
| `datagram_rate` | 数据报速率 | `s⁻¹` | 1 | [datagram_rate.unit.yaml](datagram_rate.unit.yaml) |
| `electric_charge` | 电荷量 | `C` | 4 | [electric_charge.unit.yaml](electric_charge.unit.yaml) |
| `electric_current` | 电流 | `A` | 4 | [electric_current.unit.yaml](electric_current.unit.yaml) |
| `energy` | 能量 | `J` | 5 | [energy.unit.yaml](energy.unit.yaml) |
| `entity_count` | 实体数量 | `Count` | 1 | [entity_count.unit.yaml](entity_count.unit.yaml) |
| `error_rate` | 错误发生速率 | `s⁻¹` | 1 | [error_rate.unit.yaml](error_rate.unit.yaml) |
| `event_rate` | 事件发生速率 | `s⁻¹` | 1 | [event_rate.unit.yaml](event_rate.unit.yaml) |
| `flow_rate` | 网络流速率 | `s⁻¹` | 1 | [flow_rate.unit.yaml](flow_rate.unit.yaml) |
| `frame_rate` | 帧速率 | `s⁻¹` | 1 | [frame_rate.unit.yaml](frame_rate.unit.yaml) |
| `frequency` | 频率 | `Hz` | 5 | [frequency.unit.yaml](frequency.unit.yaml) |
| `io_rate` | I/O操作速率 | `s⁻¹` | 1 | [io_rate.unit.yaml](io_rate.unit.yaml) |
| `length` | 长度 | `m` | 6 | [length.unit.yaml](length.unit.yaml) |
| `logarithmic_ratio` | 对数比 | `dB` | 1 | [logarithmic_ratio.unit.yaml](logarithmic_ratio.unit.yaml) |
| `message_rate` | 消息速率 | `s⁻¹` | 1 | [message_rate.unit.yaml](message_rate.unit.yaml) |
| `operation_rate` | 操作速率 | `s⁻¹` | 1 | [operation_rate.unit.yaml](operation_rate.unit.yaml) |
| `packet_rate` | 数据包速率 | `s⁻¹` | 1 | [packet_rate.unit.yaml](packet_rate.unit.yaml) |
| `power` | 功率 | `W` | 5 | [power.unit.yaml](power.unit.yaml) |
| `power_level` | 功率电平 | `dBm` | 2 | [power_level.unit.yaml](power_level.unit.yaml) |
| `pressure` | 压力 | `Pa` | 3 | [pressure.unit.yaml](pressure.unit.yaml) |
| `ratio` | 比率 | `!%` | 3 | [ratio.unit.yaml](ratio.unit.yaml) |
| `reactive_power` | 无功功率 | `var` | 2 | [reactive_power.unit.yaml](reactive_power.unit.yaml) |
| `record_rate` | 记录速率 | `s⁻¹` | 1 | [record_rate.unit.yaml](record_rate.unit.yaml) |
| `relative_humidity` | 相对湿度 | `%` | 1 | [relative_humidity.unit.yaml](relative_humidity.unit.yaml) |
| `request_rate` | 请求速率 | `s⁻¹` | 1 | [request_rate.unit.yaml](request_rate.unit.yaml) |
| `resistance` | 电阻 | `Ω` | 3 | [resistance.unit.yaml](resistance.unit.yaml) |
| `rotational_frequency` | 转动频率 | `r/s` | 2 | [rotational_frequency.unit.yaml](rotational_frequency.unit.yaml) |
| `session_rate` | 会话建立速率 | `s⁻¹` | 1 | [session_rate.unit.yaml](session_rate.unit.yaml) |
| `symbol_rate` | 符号率 | `Bd` | 4 | [symbol_rate.unit.yaml](symbol_rate.unit.yaml) |
| `thermodynamic_temperature` | 热力学温度 | `K` | 2 | [thermodynamic_temperature.unit.yaml](thermodynamic_temperature.unit.yaml) |
| `time` | 时间 | `s` | 8 | [time.unit.yaml](time.unit.yaml) |
| `transaction_rate` | 事务速率 | `s⁻¹` | 1 | [transaction_rate.unit.yaml](transaction_rate.unit.yaml) |
| `voltage` | 电压 | `V` | 4 | [voltage.unit.yaml](voltage.unit.yaml) |
| `volume_flow_rate` | 体积流量 | `m³/s` | 3 | [volume_flow_rate.unit.yaml](volume_flow_rate.unit.yaml) |

## 完整单位清单

| # | 单位类型 | 中文类型 | 单位名称 | 中文名称 | 单位符号 | 基准 | 换算到基准 | 配置文件 |
|---:|---|---|---|---|---:|:---:|---|---|
| 1 | `angular_velocity` | 角速度 | `radians_per_second` | 弧度每秒 | `rad/s` | Y | `1` | [angular_velocity.unit.yaml](angular_velocity.unit.yaml) |
| 2 | `angular_velocity` | 角速度 | `degrees_per_second` | 度每秒 | `°/s` | N | `0.017453292519943295` | [angular_velocity.unit.yaml](angular_velocity.unit.yaml) |
| 3 | `apparent_power` | 视在功率 | `volt_ampere` | 伏安 | `VA` | Y | `1` | [apparent_power.unit.yaml](apparent_power.unit.yaml) |
| 4 | `apparent_power` | 视在功率 | `kilovolt_ampere` | 千伏安 | `kVA` | N | `1000` | [apparent_power.unit.yaml](apparent_power.unit.yaml) |
| 5 | `binary_byte_rate` | 二进制前缀字节率 | `bytes_per_second` | 字节每秒 | `B/s` | Y | `1` | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| 6 | `binary_byte_rate` | 二进制前缀字节率 | `kibibytes_per_second` | 二进制千字节每秒 | `KiB/s` | N | `1024` | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| 7 | `binary_byte_rate` | 二进制前缀字节率 | `mebibytes_per_second` | 二进制兆字节每秒 | `MiB/s` | N | `1048576` | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| 8 | `binary_byte_rate` | 二进制前缀字节率 | `gibibytes_per_second` | 二进制吉字节每秒 | `GiB/s` | N | `1073741824` | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| 9 | `binary_byte_rate` | 二进制前缀字节率 | `tebibytes_per_second` | 二进制太字节每秒 | `TiB/s` | N | `1099511627776` | [binary_byte_rate.unit.yaml](binary_byte_rate.unit.yaml) |
| 10 | `binary_data_size` | 二进制前缀数据量 | `byte` | 字节 | `B` | Y | `1` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 11 | `binary_data_size` | 二进制前缀数据量 | `kibibyte` | 二进制千字节 | `KiB` | N | `1024` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 12 | `binary_data_size` | 二进制前缀数据量 | `mebibyte` | 二进制兆字节 | `MiB` | N | `1048576` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 13 | `binary_data_size` | 二进制前缀数据量 | `gibibyte` | 二进制吉字节 | `GiB` | N | `1073741824` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 14 | `binary_data_size` | 二进制前缀数据量 | `tebibyte` | 二进制太字节 | `TiB` | N | `1099511627776` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 15 | `binary_data_size` | 二进制前缀数据量 | `pebibyte` | 二进制拍字节 | `PiB` | N | `1125899906842624` | [binary_data_size.unit.yaml](binary_data_size.unit.yaml) |
| 16 | `bit_rate` | 比特率 | `bits_per_second` | 比特每秒 | `bit/s` | Y | `1` | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| 17 | `bit_rate` | 比特率 | `kilobits_per_second` | 千比特每秒 | `kbit/s` | N | `1000` | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| 18 | `bit_rate` | 比特率 | `megabits_per_second` | 兆比特每秒 | `Mbit/s` | N | `1000000` | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| 19 | `bit_rate` | 比特率 | `gigabits_per_second` | 吉比特每秒 | `Gbit/s` | N | `1000000000` | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| 20 | `bit_rate` | 比特率 | `terabits_per_second` | 太比特每秒 | `Tbit/s` | N | `1000000000000` | [bit_rate.unit.yaml](bit_rate.unit.yaml) |
| 21 | `byte_rate` | 字节率 | `bytes_per_second` | 字节每秒 | `B/s` | Y | `1` | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| 22 | `byte_rate` | 字节率 | `kilobytes_per_second` | 千字节每秒 | `kB/s` | N | `1000` | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| 23 | `byte_rate` | 字节率 | `megabytes_per_second` | 兆字节每秒 | `MB/s` | N | `1000000` | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| 24 | `byte_rate` | 字节率 | `gigabytes_per_second` | 吉字节每秒 | `GB/s` | N | `1000000000` | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| 25 | `byte_rate` | 字节率 | `terabytes_per_second` | 太字节每秒 | `TB/s` | N | `1000000000000` | [byte_rate.unit.yaml](byte_rate.unit.yaml) |
| 26 | `connection_rate` | 连接建立速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [connection_rate.unit.yaml](connection_rate.unit.yaml) |
| 27 | `data_size` | 数据量 | `byte` | 字节 | `B` | Y | `1` | [data_size.unit.yaml](data_size.unit.yaml) |
| 28 | `data_size` | 数据量 | `bit` | 比特 | `bit` | N | `0.125` | [data_size.unit.yaml](data_size.unit.yaml) |
| 29 | `data_size` | 数据量 | `octet` | 八位组 | `o` | N | `1` | [data_size.unit.yaml](data_size.unit.yaml) |
| 30 | `data_size` | 数据量 | `kilobyte` | 千字节 | `kB` | N | `1000` | [data_size.unit.yaml](data_size.unit.yaml) |
| 31 | `data_size` | 数据量 | `megabyte` | 兆字节 | `MB` | N | `1000000` | [data_size.unit.yaml](data_size.unit.yaml) |
| 32 | `data_size` | 数据量 | `gigabyte` | 吉字节 | `GB` | N | `1000000000` | [data_size.unit.yaml](data_size.unit.yaml) |
| 33 | `data_size` | 数据量 | `terabyte` | 太字节 | `TB` | N | `1000000000000` | [data_size.unit.yaml](data_size.unit.yaml) |
| 34 | `data_size` | 数据量 | `petabyte` | 拍字节 | `PB` | N | `1000000000000000` | [data_size.unit.yaml](data_size.unit.yaml) |
| 35 | `datagram_rate` | 数据报速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [datagram_rate.unit.yaml](datagram_rate.unit.yaml) |
| 36 | `electric_charge` | 电荷量 | `coulomb` | 库仑 | `C` | Y | `1` | [electric_charge.unit.yaml](electric_charge.unit.yaml) |
| 37 | `electric_charge` | 电荷量 | `millicoulomb` | 毫库仑 | `mC` | N | `0.001` | [electric_charge.unit.yaml](electric_charge.unit.yaml) |
| 38 | `electric_charge` | 电荷量 | `ampere_hour` | 安培小时 | `A·h` | N | `3600` | [electric_charge.unit.yaml](electric_charge.unit.yaml) |
| 39 | `electric_charge` | 电荷量 | `milliampere_hour` | 毫安培小时 | `mA·h` | N | `3.6` | [electric_charge.unit.yaml](electric_charge.unit.yaml) |
| 40 | `electric_current` | 电流 | `ampere` | 安培 | `A` | Y | `1` | [electric_current.unit.yaml](electric_current.unit.yaml) |
| 41 | `electric_current` | 电流 | `microampere` | 微安培 | `μA` | N | `0.000001` | [electric_current.unit.yaml](electric_current.unit.yaml) |
| 42 | `electric_current` | 电流 | `milliampere` | 毫安培 | `mA` | N | `0.001` | [electric_current.unit.yaml](electric_current.unit.yaml) |
| 43 | `electric_current` | 电流 | `kiloampere` | 千安培 | `kA` | N | `1000` | [electric_current.unit.yaml](electric_current.unit.yaml) |
| 44 | `energy` | 能量 | `joule` | 焦耳 | `J` | Y | `1` | [energy.unit.yaml](energy.unit.yaml) |
| 45 | `energy` | 能量 | `kilojoule` | 千焦耳 | `kJ` | N | `1000` | [energy.unit.yaml](energy.unit.yaml) |
| 46 | `energy` | 能量 | `watt_hour` | 瓦特小时 | `Wh` | N | `3600` | [energy.unit.yaml](energy.unit.yaml) |
| 47 | `energy` | 能量 | `kilowatt_hour` | 千瓦时 | `kWh` | N | `3600000` | [energy.unit.yaml](energy.unit.yaml) |
| 48 | `energy` | 能量 | `kilocalorie` | 千卡 | `kcal` | N | `4184` | [energy.unit.yaml](energy.unit.yaml) |
| 49 | `entity_count` | 实体数量 | `count` | 计数 | `Count` | Y | `1` | [entity_count.unit.yaml](entity_count.unit.yaml) |
| 50 | `error_rate` | 错误发生速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [error_rate.unit.yaml](error_rate.unit.yaml) |
| 51 | `event_rate` | 事件发生速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [event_rate.unit.yaml](event_rate.unit.yaml) |
| 52 | `flow_rate` | 网络流速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [flow_rate.unit.yaml](flow_rate.unit.yaml) |
| 53 | `frame_rate` | 帧速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [frame_rate.unit.yaml](frame_rate.unit.yaml) |
| 54 | `frequency` | 频率 | `hertz` | 赫兹 | `Hz` | Y | `1` | [frequency.unit.yaml](frequency.unit.yaml) |
| 55 | `frequency` | 频率 | `kilohertz` | 千赫兹 | `kHz` | N | `1000` | [frequency.unit.yaml](frequency.unit.yaml) |
| 56 | `frequency` | 频率 | `megahertz` | 兆赫兹 | `MHz` | N | `1000000` | [frequency.unit.yaml](frequency.unit.yaml) |
| 57 | `frequency` | 频率 | `gigahertz` | 吉赫兹 | `GHz` | N | `1000000000` | [frequency.unit.yaml](frequency.unit.yaml) |
| 58 | `frequency` | 频率 | `terahertz` | 太赫兹 | `THz` | N | `1000000000000` | [frequency.unit.yaml](frequency.unit.yaml) |
| 59 | `io_rate` | I/O操作速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [io_rate.unit.yaml](io_rate.unit.yaml) |
| 60 | `length` | 长度 | `metre` | 米 | `m` | Y | `1` | [length.unit.yaml](length.unit.yaml) |
| 61 | `length` | 长度 | `nanometre` | 纳米 | `nm` | N | `0.000000001` | [length.unit.yaml](length.unit.yaml) |
| 62 | `length` | 长度 | `micrometre` | 微米 | `μm` | N | `0.000001` | [length.unit.yaml](length.unit.yaml) |
| 63 | `length` | 长度 | `millimetre` | 毫米 | `mm` | N | `0.001` | [length.unit.yaml](length.unit.yaml) |
| 64 | `length` | 长度 | `centimetre` | 厘米 | `cm` | N | `0.01` | [length.unit.yaml](length.unit.yaml) |
| 65 | `length` | 长度 | `kilometre` | 千米 | `km` | N | `1000` | [length.unit.yaml](length.unit.yaml) |
| 66 | `logarithmic_ratio` | 对数比 | `decibel` | 分贝 | `dB` | Y | `1` | [logarithmic_ratio.unit.yaml](logarithmic_ratio.unit.yaml) |
| 67 | `message_rate` | 消息速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [message_rate.unit.yaml](message_rate.unit.yaml) |
| 68 | `operation_rate` | 操作速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [operation_rate.unit.yaml](operation_rate.unit.yaml) |
| 69 | `packet_rate` | 数据包速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [packet_rate.unit.yaml](packet_rate.unit.yaml) |
| 70 | `power` | 功率 | `watt` | 瓦特 | `W` | Y | `1` | [power.unit.yaml](power.unit.yaml) |
| 71 | `power` | 功率 | `microwatt` | 微瓦 | `μW` | N | `0.000001` | [power.unit.yaml](power.unit.yaml) |
| 72 | `power` | 功率 | `milliwatt` | 毫瓦 | `mW` | N | `0.001` | [power.unit.yaml](power.unit.yaml) |
| 73 | `power` | 功率 | `kilowatt` | 千瓦 | `kW` | N | `1000` | [power.unit.yaml](power.unit.yaml) |
| 74 | `power` | 功率 | `megawatt` | 兆瓦 | `MW` | N | `1000000` | [power.unit.yaml](power.unit.yaml) |
| 75 | `power_level` | 功率电平 | `decibel_milliwatt` | 分贝毫瓦 | `dBm` | Y | `1` | [power_level.unit.yaml](power_level.unit.yaml) |
| 76 | `power_level` | 功率电平 | `decibel_watt` | 分贝瓦 | `dBW` | N | `value + 30` | [power_level.unit.yaml](power_level.unit.yaml) |
| 77 | `pressure` | 压力 | `pascal` | 帕斯卡 | `Pa` | Y | `1` | [pressure.unit.yaml](pressure.unit.yaml) |
| 78 | `pressure` | 压力 | `hectopascal` | 百帕 | `hPa` | N | `100` | [pressure.unit.yaml](pressure.unit.yaml) |
| 79 | `pressure` | 压力 | `kilopascal` | 千帕 | `kPa` | N | `1000` | [pressure.unit.yaml](pressure.unit.yaml) |
| 80 | `ratio` | 比率 | `one` | 一 | `!%` | Y | `1` | [ratio.unit.yaml](ratio.unit.yaml) |
| 81 | `ratio` | 比率 | `percent` | 百分比 | `%` | N | `0.01` | [ratio.unit.yaml](ratio.unit.yaml) |
| 82 | `ratio` | 比率 | `per_mille` | 千分比 | `‰` | N | `0.001` | [ratio.unit.yaml](ratio.unit.yaml) |
| 83 | `reactive_power` | 无功功率 | `var` | 乏 | `var` | Y | `1` | [reactive_power.unit.yaml](reactive_power.unit.yaml) |
| 84 | `reactive_power` | 无功功率 | `kilovar` | 千乏 | `kvar` | N | `1000` | [reactive_power.unit.yaml](reactive_power.unit.yaml) |
| 85 | `record_rate` | 记录速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [record_rate.unit.yaml](record_rate.unit.yaml) |
| 86 | `relative_humidity` | 相对湿度 | `percent_relative_humidity` | 相对湿度百分比 | `%` | Y | `1` | [relative_humidity.unit.yaml](relative_humidity.unit.yaml) |
| 87 | `request_rate` | 请求速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [request_rate.unit.yaml](request_rate.unit.yaml) |
| 88 | `resistance` | 电阻 | `ohm` | 欧姆 | `Ω` | Y | `1` | [resistance.unit.yaml](resistance.unit.yaml) |
| 89 | `resistance` | 电阻 | `kiloohm` | 千欧姆 | `kΩ` | N | `1000` | [resistance.unit.yaml](resistance.unit.yaml) |
| 90 | `resistance` | 电阻 | `megaohm` | 兆欧姆 | `MΩ` | N | `1000000` | [resistance.unit.yaml](resistance.unit.yaml) |
| 91 | `rotational_frequency` | 转动频率 | `revolutions_per_second` | 转每秒 | `r/s` | Y | `1` | [rotational_frequency.unit.yaml](rotational_frequency.unit.yaml) |
| 92 | `rotational_frequency` | 转动频率 | `revolutions_per_minute` | 转每分钟 | `r/min` | N | `0.016666666666666667` | [rotational_frequency.unit.yaml](rotational_frequency.unit.yaml) |
| 93 | `session_rate` | 会话建立速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [session_rate.unit.yaml](session_rate.unit.yaml) |
| 94 | `symbol_rate` | 符号率 | `baud` | 波特 | `Bd` | Y | `1` | [symbol_rate.unit.yaml](symbol_rate.unit.yaml) |
| 95 | `symbol_rate` | 符号率 | `kilobaud` | 千波特 | `kBd` | N | `1000` | [symbol_rate.unit.yaml](symbol_rate.unit.yaml) |
| 96 | `symbol_rate` | 符号率 | `megabaud` | 兆波特 | `MBd` | N | `1000000` | [symbol_rate.unit.yaml](symbol_rate.unit.yaml) |
| 97 | `symbol_rate` | 符号率 | `gigabaud` | 吉波特 | `GBd` | N | `1000000000` | [symbol_rate.unit.yaml](symbol_rate.unit.yaml) |
| 98 | `thermodynamic_temperature` | 热力学温度 | `kelvin` | 开尔文 | `K` | Y | `1` | [thermodynamic_temperature.unit.yaml](thermodynamic_temperature.unit.yaml) |
| 99 | `thermodynamic_temperature` | 热力学温度 | `degree_celsius` | 摄氏度 | `°C` | N | `value + 273.15` | [thermodynamic_temperature.unit.yaml](thermodynamic_temperature.unit.yaml) |
| 100 | `time` | 时间 | `second` | 秒 | `s` | Y | `1` | [time.unit.yaml](time.unit.yaml) |
| 101 | `time` | 时间 | `nanosecond` | 纳秒 | `ns` | N | `0.000000001` | [time.unit.yaml](time.unit.yaml) |
| 102 | `time` | 时间 | `microsecond` | 微秒 | `μs` | N | `0.000001` | [time.unit.yaml](time.unit.yaml) |
| 103 | `time` | 时间 | `millisecond` | 毫秒 | `ms` | N | `0.001` | [time.unit.yaml](time.unit.yaml) |
| 104 | `time` | 时间 | `centisecond` | 厘秒 | `cs` | N | `0.01` | [time.unit.yaml](time.unit.yaml) |
| 105 | `time` | 时间 | `minute` | 分钟 | `min` | N | `60` | [time.unit.yaml](time.unit.yaml) |
| 106 | `time` | 时间 | `hour` | 小时 | `h` | N | `3600` | [time.unit.yaml](time.unit.yaml) |
| 107 | `time` | 时间 | `day` | 天 | `d` | N | `86400` | [time.unit.yaml](time.unit.yaml) |
| 108 | `transaction_rate` | 事务速率 | `reciprocal_second` | 秒的负一次方 | `s⁻¹` | Y | `1` | [transaction_rate.unit.yaml](transaction_rate.unit.yaml) |
| 109 | `voltage` | 电压 | `volt` | 伏特 | `V` | Y | `1` | [voltage.unit.yaml](voltage.unit.yaml) |
| 110 | `voltage` | 电压 | `microvolt` | 微伏 | `μV` | N | `0.000001` | [voltage.unit.yaml](voltage.unit.yaml) |
| 111 | `voltage` | 电压 | `millivolt` | 毫伏 | `mV` | N | `0.001` | [voltage.unit.yaml](voltage.unit.yaml) |
| 112 | `voltage` | 电压 | `kilovolt` | 千伏 | `kV` | N | `1000` | [voltage.unit.yaml](voltage.unit.yaml) |
| 113 | `volume_flow_rate` | 体积流量 | `cubic_metres_per_second` | 立方米每秒 | `m³/s` | Y | `1` | [volume_flow_rate.unit.yaml](volume_flow_rate.unit.yaml) |
| 114 | `volume_flow_rate` | 体积流量 | `cubic_metres_per_minute` | 立方米每分钟 | `m³/min` | N | `0.016666666666666667` | [volume_flow_rate.unit.yaml](volume_flow_rate.unit.yaml) |
| 115 | `volume_flow_rate` | 体积流量 | `litres_per_second` | 升每秒 | `L/s` | N | `0.001` | [volume_flow_rate.unit.yaml](volume_flow_rate.unit.yaml) |
