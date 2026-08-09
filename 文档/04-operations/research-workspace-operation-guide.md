# Research 与 Library 操作说明

## 核心原则

系统顶层只使用两个研究资产名称：

```text
Research
Library
```

- `Research` 是创建、修改、组合、验证和测试的地方。
- `Library` 是保存已发布版本、管理使用情况和跨 Research 复用的地方。
- Research 工作卡片和 Library 资产是两个身份不同的对象。
- 发布时两者内容相同；发布完成后彼此独立，不再自动同步。

## 一级导航

```text
Research / Library / Runs / Data Catalog / Agent Monitor / Approvals / Settings
```

## Research 列表

`/research` 显示所有 Research。每张卡片只汇总：

```text
Universe / Factor / Alpha / Data / Strategy
```

用户通过 `New Research` 创建，通过 `Open Research` 进入。

## 单个 Research

```text
Overview / Universe / Factor / Alpha / Data / Strategy / Runs
```

页面顶部只显示：

```text
RESEARCH
Research 名称
```

### Research 本地工作卡片

本地卡片显示：

```text
Source: Current Research
Research Draft 或 Validated
```

主要操作：

```text
Edit / Validate / Publish to Library
```

编辑不会覆盖已发布版本。后台会生成新的工作版本，但普通 UI 不展示复杂版本术语。

### Library 引用卡片

从 Library 添加后，Research 保存对指定 Library 版本的引用，不复制一份无身份的数据。

卡片显示：

```text
Source: Library · v2
Current Research Use
```

只提供：

```text
View / Replace / Copy and Edit
```

引用是只读的。`Copy and Edit` 会创建新的 Research 工作卡片并切断自动同步关系。

## 发布生命周期

```text
Create in Research
→ Save Draft
→ Validate
→ Publish to Library
```

发布会创建新的不可变 Library 资产，并记录：

```text
Library version
Source Research
Source work object
Content hash
Published time
```

发布不会把 Research 工作对象原地改成 Library 对象。

例如：

```text
Research: pct_change(close, 20)
→ Publish
Library v1: pct_change(close, 20)

Research 后续编辑: pct_change(close, 60)
Library v1 仍然是: pct_change(close, 20)

再次验证并发布
Library v2: pct_change(close, 60)
```

其他 Research 已经引用的 v1 不会自动升级到 v2。

## Library

Library 页面包含：

```text
Universe / Factor / Alpha / Requirements / Strategy
```

只展示已发布资产。Research 草稿不会出现。

主要操作：

```text
Use in Research / Create New Version / View Usage / Details
```

`Create New Version` 会先选择目标 Research，再在那里建立新的工作卡片。

## Requirement 数据自动维护

用户只负责定义 Requirement，数据准备由后端维护：

```text
Requirement Library + Active Research
→ Backend Coverage Scan
→ Idempotent Provider Task
→ Download / Validate / Manifest Commit
→ Research 与 Library 实时状态
```

- Library Requirement 即使暂时没有被 Research 引用，也会被扫描。
- 普通缺口显示 `Queued / Preparing / Checking`，不显示 `Needs Attention`，也没有 `Complete Missing Data` 或 `Prepare` 按钮。
- Research Data 与 Library 卡片显示同一任务的 `Live Download` 信息，包括阶段、分区、行数、当前范围、ETA 和运行时间。
- 只有自动维护的终态错误或 Provider / Contract 不支持时显示 `Failed / Unavailable`；用户此时修改 Requirement 或修复运行环境，不负责手动启动下载。
- 页面刷新和 Tab 切换只读取状态，不触发或重复创建下载任务。

完整状态与排障说明见 [Requirement 数据自动维护与实时进度](./requirement-data-maintenance.md)。

## 统一对象规则

Universe、Factor、Alpha 和 Requirements 都遵循相同身份模型：

```text
Research 本地对象
→ 验证
→ 发布不可变 Library 版本
```

或：

```text
Library 指定版本
→ Research 只读引用
→ Copy and Edit
→ 新 Research 草稿
```

Strategy Tab 已保留，但在研究流程和审批边界完成前不开放创建与发布。

## Runs

Research 内仅创建和查看：

```text
Factor Test / Alpha Test
```

Run 创建时，后台固定当前 Research 使用的 Universe、Factor、Alpha、Requirements 和数据版本。普通 UI 不展示 Bundle、fingerprint、冻结术语或内部 ID。

Backtest 后续作为独立流程接入，不提前混入 Research 测试页面。
