# Qlib Alpha158 股票兼容模式

## 当前边界

首版已实现 `qlib.alpha158_without_vwap`，显示名称固定为：

```text
Qlib Alpha158-compatible (VWAP excluded)
```

它调用 Qlib 0.9.7 官方 `Alpha158DL.get_feature_config`，仅从价格字段中移除
`VWAP0`，所以输出 157 个因子。结果元数据同时写入：

```text
compatibility_mode = VWAP_EXCLUDED
is_standard_alpha158 = false
excluded_factors = [VWAP0]
```

任何调用方都不得把这个结果显示成标准 Alpha158。未来 Canonical Bars 提供真实
VWAP 后，应新增标准 pack identity，不能修改或覆盖已经生成的兼容模式缓存。

## 输入合同

Importer 只读取 DataTube `READY` 的不可变 `bars.v1` Manifest，不访问 Yahoo、
Finnhub 或其他外部行情接口。每个 Manifest 必须满足：

- instrument 为 `equity:*`；
- frequency 为 `1d`；
- 包含 `open/high/low/close/volume`；
- 至少 60 根唯一日线；
- 所有股票使用同一个 adjustment policy；
- Manifest 的物理文件、checksum、schema 和质量校验通过。

DataTube 的 OpenBB/yfinance 日线准备任务已经能产生这种 Manifest，因此 Qlib 不负责
下载或复权。

## 输出与缓存

默认输出目录为：

```text
storage/factor_cache/qlib/alpha158_without_vwap/{cache_id}/
  factors.parquet
  manifest.json
```

`factors.parquet` 是宽表：

```text
datetime, instrument, KMID, KLEN, ..., VSUMD60
```

`cache_id` 冻结以下身份：

- 输入 Manifest ID 和 Manifest hash；
- Input Bundle ID（如有）；
- 请求日期范围；
- adjustment policy；
- Qlib version；
- Importer version；
- factor pack identity。

缓存是不可变的。再次运行相同输入直接命中缓存；`--force` 只重新计算并检查确定性，
不会覆盖已有缓存。

## 安装与执行

建议使用独立 Python 环境，避免 Qlib 的研究依赖进入 DataTube 主服务：

```powershell
python -m venv .qlib-venv
.\.qlib-venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-qlib.txt
```

一个股票一个 Manifest；可以重复传参，也可以逗号分隔：

```powershell
.\.qlib-venv\Scripts\python.exe scripts\run_qlib_alpha158.py `
  --manifest-id manifest_a,manifest_b `
  --input-bundle-id artifact_bundle_id `
  --start-time 2021-01-01 `
  --end-time 2025-12-31
```

确定性端到端验证：

```powershell
.\.qlib-venv\Scripts\python.exe scripts\verify_qlib_alpha158.py
```

验证器创建临时股票 `bars.v1` Manifest，运行真实 Qlib 表达式引擎，检查 157 个输出
字段、60 日滚动因子、VWAP 排除标记和第二次缓存命中，然后清理临时数据。

## 后续 VWAP 接入点

后续工作只需：

1. Canonical Bars schema/adapter 保留 provider-reported VWAP；
2. 新增 `qlib.alpha158` pack，要求 OHLCV + VWAP；
3. Alpha158 配置把 `VWAP` 加回 price feature；
4. 使用新的 pack ID、schema/version 和 cache identity；
5. 保留 `qlib.alpha158_without_vwap` 历史结果只读可复现。

当前模块只负责计算、规范化和缓存。正式 Research Library 发布、Factor Evaluation
Run 绑定和 UI Factor Pack 入口仍应通过 DataTube 受控 Research API 实现，不能由
Importer 直接写数据库或绕过 Session/Run 授权。

