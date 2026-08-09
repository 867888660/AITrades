# API 密钥存储与迁移

更新日期：2026-07-12

## 当前模型

敏感字段统一由 `services.secure_settings` 管理：

```text
finnhub_api_keys / active_finnhub_api_key
coingecko_api_key
llm_api_key
openbb_fred_api_key
```

Windows 使用当前用户 DPAPI，密文保存在 `web_settings.secrets.json`。普通 `web_settings.json` 不保存上述字段。DPAPI 密文只能由同一 Windows 用户上下文解密，因此迁移机器或用户前必须重新录入密钥。

非 Windows 环境使用 Fernet，并单独保护 `.datatube_secret.key`。两个文件必须一起备份，且不能提交 Git。

## Settings API

`GET /api/settings` 和保存后的响应只返回：

```text
has_active_finnhub_api_key
finnhub_api_key_count
has_coingecko_api_key
has_llm_api_key
has_openbb_fred_api_key
```

不会返回原始值。Settings 页面密码框和 Finnhub textarea 始终为空，仅显示“已保存”状态。

保存规则：

- 非空新值：替换对应密钥；
- 空白值：保留已有密钥；
- 勾选明确的清除选项：删除对应密钥；
- 保存使用临时文件、flush、fsync 和原子 replace；
- Windows 成功迁移到 DPAPI 后删除旧 Fernet key 文件。

## OpenBB/FRED

FRED 密钥不作为 HTTP 查询参数从 DataTube 传给 OpenBB。隔离服务启动器从 DPAPI 解密，在创建 OpenBB 子进程时设置 `FRED_API_KEY`。官方 OpenBB API 再通过 `provider=fred` 使用凭据。

测试：

```http
GET /api/research/data/providers/openbb/fred/series?symbol=DGS10&limit=1
```

响应不得包含凭据，只包含 gateway、provider、series、结果、warning 和延迟。

## 轮换

1. 在 Settings 输入新值并保存；
2. 确认“已加密保存”状态；
3. 对外部子进程型 Provider（如 OpenBB）重启服务；
4. 运行最小只读测试；
5. 在上游控制台撤销旧密钥；
6. 检查日志、普通配置和 Git tracked files 不含旧值。

不要先撤销旧密钥再保存新值，否则会扩大数据采集中断窗口。
