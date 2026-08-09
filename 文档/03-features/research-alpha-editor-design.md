# Research Alpha 编辑与验证系统设计

状态：设计完成，待实现  
版本：v1.1  
日期：2026-07-29

实施蓝图：[Research Alpha 编辑器实施蓝图](../04-operations/research-alpha-editor-implementation-plan.md)

## 1. 设计结论

Alpha 使用与 Factor 相同的编辑结构和生命周期：

```text
创建或恢复 Draft
→ 自动备份
→ 服务端 Definition Checks
→ 保存 Draft
→ 编译精确数据 Requirement
→ 准备并固定数据 Manifest
→ 运行真实值 Preview
→ 校验 Draft / Dependency / Preview 指纹
→ Validate
→ 创建不可变 Alpha Definition
→ 固定到当前 Research
→ 以 PROJECT 范围显示在正常 Alpha Library
```

Alpha 只保留计算语义上的必要差异：

- Factor 的输入是数据变量；Alpha 的输入是固定版本的 Validated Factor。
- Factor 的公式是 Factor Formula v4；Alpha 的公式是受控的加权组件组合。
- Factor Preview 展示逐标的 Factor 值；Alpha Preview 展示横截面 raw score、rank 和 percentile。
- Factor 输出是测量值或事件；Alpha 输出固定为 Prediction Score。

本次设计不修改现有 AlphaEngine 的计算语义，不引入任意 Alpha DSL，不修改 Strategy、Paper 或 Live 边界。

## 2. 目标与非目标

### 2.1 目标

1. Alpha 与 Factor 使用相同的信息结构、保存体验、状态语言和按钮顺序。
2. Alpha 草稿允许不完整保存、自动备份、恢复编辑和显式丢弃。
3. 所有 Alpha 检查由后端执行，前端只负责展示结果。
4. Validate 必须持有与当前 Draft 完全匹配的真实 Preview 证据。
5. Alpha 必须固定精确 Factor Definition ID、Version 和 Spec Hash。
6. 任何 Factor、Universe、Manifest、Engine 或时间范围变化都使旧 Preview 失效。
7. 已验证 Alpha 版本保持不可变；修改必须创建新语义版本。
8. 现有正式 Research Run、Frozen Bundle、Artifact 和 Agent API 保持兼容。

### 2.2 非目标

- 不允许在 Alpha 编辑器内编写任意 Python、SQL 或自由形式代码。
- 不在 Alpha Preview 中运行完整 Alpha Evaluation、Portfolio 或 Backtest。
- 不自动创建 Strategy。
- 不自动扩大或恢复 Research Session Policy；内部授权兼容记录由 Session 服务管理。
- 不发布为 GLOBAL Library 定义。
- 不覆盖或删除旧 Definition、Preview、Manifest、Artifact、Bundle、Run、审计或 Lineage。
- 不在本阶段重构已经验收的 FactorDraftService 和 FactorPreviewService。

## 3. 当前基线与问题

### 3.1 Factor 当前能力

Factor 已具有完整的本地编辑器生命周期：

- `factor_drafts` 保存可变且允许不完整的编辑文档。
- Draft 使用 fingerprint 进行并发和失效控制。
- 服务端编译 Input、Formula、Parameters 和 Output。
- Preview 前生成精确 Requirement 并解析固定 Manifest。
- Preview 计算真实 Factor 值并保存分析结果。
- Validate 要求当前 Draft fingerprint 与 Preview fingerprint 同时匹配。
- Validate 后生成不可变 Definition，并固定到当前 Research。

### 3.2 Alpha 当前能力

Alpha 当前编辑器直接调用通用 Definition 创建接口：

```text
Alpha Form
→ POST /api/research/definitions
→ 创建 research_definitions 中的 DRAFT
→ TRACK_DRAFT Project Ref
→ 通用 Validate 直接切换为 VALIDATED
```

当前缺少：

- 独立 Alpha Draft
- 自动备份
- Draft fingerprint
- 服务端 Definition Checks 面板
- 精确 Requirement 编译
- 真实 Alpha Preview
- Preview fingerprint
- Dependency stale 检查
- Validate 前的真实结果证据
- 与 Factor 一致的 Saved Work 卡片

### 3.3 当前数据兼容性

2026-07-28 只读检查显示：

- 6 个 `VALIDATED` Factor
- 1 个 `VALIDATED` Alpha
- 0 个 Alpha DRAFT

因此不需要迁移已有 Alpha 草稿。现有 Validated Alpha 保持原样；用户基于旧 Alpha 创建新版本时，系统把旧 AlphaSpec 转换为新的 AlphaDraft 文档。

## 4. 用户体验设计

### 4.1 Alpha 页面

Alpha 页与 Factor 页保持同一结构：

```text
ALPHA
Combine validated Factors into a prediction definition.

[Add from Library] [Create in Research]

SAVED WORK
  Alpha Draft Cards

VALIDATED / LIBRARY REFERENCES
  Alpha Definition Cards
```

Alpha Draft 卡片显示：

- Name
- Changes backed up / 更新时间
- Input：Factor 数量及名称
- Formula：生成后的加权表达式
- Output：Prediction Score
- Preview：Not Run / Ready / Stale
- `Discard Draft`
- `Continue editing`

Validated Alpha 卡片继续显示：

- 精确版本
- Input Factors
- Formula 摘要
- Output
- Research / Library 来源
- Test、Details、新版本等操作

### 4.2 Alpha 编辑器布局

编辑器复用 Factor 的视觉层级、状态栏、双栏布局、检查面板和底部操作区。

### 顶部状态栏

```text
Research        当前 Research
Version         自动语义版本
Draft State     Unsaved / Changes backed up / Preview ready / Validated
Engine          alpha-engine.v2
```

### 左侧定义区

#### A. Identity

- Name
- Description
- Version（隐藏或只读，系统自动生成）

描述 Alpha 测量或预测的含义，不描述下单、仓位或风险控制。

#### B. Input

每个 Input 是一个固定 Factor：

- Variable Name
- Factor Definition
- Factor Version（只读，由 Definition 选择决定）
- Factor State（必须是 VALIDATED）
- Factor Output Type / Unit
- Factor Spec Hash（Advanced）
- Remove

只允许选择：

- 当前 Research 已固定的 Validated Factor
- 当前 Research 已引用的 Library Factor
- 当前 Project 自己拥有的 Validated Factor

不允许选择：

- DRAFT Factor
- 已归档或 Superseded 且不可用于新研究的 Factor
- 其他 Project 私有且未通过 Library 引用的 Factor
- 无法解析精确 Version 的 Factor

#### C. Formula

Alpha v1 继续使用受控组件组合，不开放任意源码。

每行组件包含：

- Factor Variable
- Transform：`RAW` 或 `CS_RANK`
- Score Direction：`HIGH_VALUE_HIGH_SCORE` 或 `LOW_VALUE_HIGH_SCORE`
- Weight：有限浮点数，允许负数

编辑器自动生成只读公式：

```text
alpha_score =
  0.6 * CS_RANK(momentum, ascending=true)
  + -0.4 * RAW(volatility)
```

新组件默认使用：

```text
Transform       = CS_RANK
Score Direction = HIGH_VALUE_HIGH_SCORE
Weight          = 1
```

UI 不直接展示含义模糊的 Ascending / Descending。提交给现有 AlphaSpec 时映射为：

```text
HIGH_VALUE_HIGH_SCORE → ascending=true
LOW_VALUE_HIGH_SCORE  → ascending=false
```

`Score Direction` 只对 `CS_RANK` 生效。选择 `RAW` 时：

- UI 禁用 Score Direction。
- 文档规范化为 `ascending=true`。
- 如需反转 RAW 因子含义，使用负 Weight。
- 切换回 `CS_RANK` 时恢复用户上一次显式选择的 Score Direction；该纯 UI 记忆不进入 Draft fingerprint。

公式显示文本不是权威输入。权威输入始终是规范化后的 Component List，避免显示格式变化导致指纹变化。

不增加自由形式 Alpha Formula Parser，避免前端表达能力超过 AlphaEngine。

#### D. Output

Alpha v1 的 Output 由引擎固定：

- Output：Prediction Score
- Raw Score：加权组件和
- Rank Method：AVERAGE
- Output Scale：PERCENTILE
- Missing Policy：EXCLUDE
- Available Time：所有组件的最晚 available_time
- Universe Semantics：固定 Universe Snapshot 的横截面

用户可编辑：

- Display Name
- Minimum Coverage
- Minimum Instruments

### 右侧检查与 Preview 区

#### Definition Checks

与 Factor 一致显示：

- Ready for Preview / Needs attention
- Error 数量
- Warning 数量
- 稳定错误码
- 对应字段高亮

#### Value Preview

用户选择 UTC Start / End，运行真实 Alpha Preview。

展示：

- Preview 状态
- Universe Snapshot
- 时间范围
- Factor 数量
- 生成的精确 Requirement
- Manifest IDs
- Time Points
- 平均 Coverage
- 最低 Coverage
- 最新横截面 raw score / rank / percentile
- 每个 Factor 的最新贡献
- 缺失 Factor 或被排除标的
- Preview fingerprint
- Draft fingerprint
- Dependency fingerprint
- Engine / Code Hash

### 底部固定操作区

按钮顺序与 Factor 一致：

```text
[Save Draft] [Run Preview] [Validate Alpha]
```

状态规则：

- `Save Draft`：始终可用；允许保存不完整文档。
- `Run Preview`：只有 Draft 已保存且 `can_preview=true` 时可用。
- `Validate Alpha`：只有当前 Preview 为 READY 且全部指纹仍匹配时可用。

## 5. Alpha Draft 文档契约

建议版本：

```text
alpha_draft.v1       持久化容器
alpha_draft.v2       编辑器文档
alpha_preview.v1     Preview 证据
alpha_formula.v2     现有组件组合合同
```

示例：

```json
{
  "schema_version": "alpha_draft.v2",
  "identity": {
    "name": "quality_momentum_alpha",
    "description": "Ranks instruments using momentum and volatility-adjusted quality.",
    "version": "1.0.0"
  },
  "components": [
    {
      "variable_name": "momentum",
      "factor_definition_id": "factor_...",
      "factor_version": "1.0.0",
      "weight": 0.6,
      "transform": "CS_RANK",
      "ascending": true
    },
    {
      "variable_name": "volatility",
      "factor_definition_id": "factor_...",
      "factor_version": "2.1.0",
      "weight": -0.4,
      "transform": "CS_RANK",
      "ascending": true
    }
  ],
  "formula": {
    "model": "WEIGHTED_SUM"
  },
  "output": {
    "display_name": "Quality Momentum Score",
    "kind": "PREDICTION_SCORE"
  },
  "advanced": {
    "minimum_coverage": 0.8,
    "minimum_cross_section_size": 3,
    "missing_policy": "EXCLUDE",
    "rank_method": "AVERAGE",
    "output_scale": "PERCENTILE"
  }
}
```

Draft 文档不保存 `factor_spec_hash` 作为用户输入。服务端根据 ID + Version 解析并固定 Spec Hash，防止客户端伪造依赖哈希。

## 6. 编译后的 AlphaSpec

服务端将 AlphaDraft 编译为现有 AlphaSpec：

```json
{
  "name": "quality_momentum_alpha",
  "version": "1.0.0",
  "components": [
    {
      "factor_definition_id": "factor_...",
      "factor_version": "1.0.0",
      "factor_spec_hash": "sha256...",
      "factor_name": "momentum",
      "weight": 0.6,
      "transform": "CS_RANK",
      "ascending": true
    }
  ],
  "universe_snapshot_id": "snapshot_...",
  "minimum_coverage": 0.8,
  "minimum_cross_section_size": 3,
  "missing_policy": "EXCLUDE",
  "rank_method": "AVERAGE",
  "output_scale": "PERCENTILE",
  "engine_version": "alpha-engine.v2",
  "code_hash": "..."
}
```

AlphaDraft 不把 Universe Snapshot 作为可随意选择的独立定义属性。Preview 和 Validate 使用当前 Research 的固定 Primary Universe Snapshot；编译结果将该 Snapshot ID 写入最终 AlphaSpec。

如未来允许同一 Research 多个 Universe，应在 Input 区显式选择 Universe Binding，而不是静默使用任意最新 Snapshot。

## 7. 状态模型

### 7.1 Draft 持久化状态

```text
DRAFT
  ├─ update → DRAFT
  ├─ discard → DISCARDED
  └─ validate → VALIDATED

VALIDATED
  └─ immutable；修改必须创建新 Draft / 新版本

DISCARDED
  └─ terminal；保留审计事实，不物理删除
```

### 7.2 Preview 派生状态

```text
NOT_RUN
READY
STALE
VALIDATED
FAILED
```

规则：

- 新 Draft：`NOT_RUN`
- Preview 成功且依赖完全匹配：`READY`
- Draft、Factor、Universe、Manifest、Engine 或范围变化：`STALE`
- Preview 被成功用于 Validate：`VALIDATED`
- 计算或数据检查失败：`FAILED`

### 7.3 编辑器用户状态

```text
Unsaved changes
Backing up
Changes backed up
Needs attention
Ready for Preview
Preview running
Preview ready
Preview stale
Alpha validated
```

## 8. 服务端 Definition Checks

返回结构与 Factor 保持一致：

```json
{
  "definition_checks_passed": true,
  "can_compile": true,
  "can_preview": true,
  "can_validate": false,
  "can_save_alpha": true,
  "preview_required": true,
  "preview_status": "NOT_RUN",
  "summary": {
    "errors": 0,
    "warnings": 0
  },
  "diagnostics": [],
  "draft_fingerprint": "...",
  "normalized_components": [],
  "dependency_closure": [],
  "compiled_alpha_spec": {}
}
```

### 8.1 Blocking Errors

建议稳定错误码：

| Code | 条件 |
|---|---|
| `ALPHA_NAME_REQUIRED` | Name 为空 |
| `ALPHA_VERSION_REQUIRED` | Version 为空 |
| `ALPHA_LIBRARY_SCOPE_UNSUPPORTED` | Research AlphaDraft 尝试使用非 PROJECT 范围 |
| `ALPHA_VERSION_CONFLICT` | 同一 Alpha 名称和版本已存在于活动 Draft 或不可变 Definition |
| `ALPHA_COMPONENT_REQUIRED` | 没有组件 |
| `ALPHA_COMPONENT_LIMIT_EXCEEDED` | 组件数量超过 Capability 上限 |
| `ALPHA_COMPONENT_VARIABLE_REQUIRED` | Variable Name 为空 |
| `ALPHA_COMPONENT_VARIABLE_DUPLICATE` | Variable Name 重复 |
| `ALPHA_FACTOR_REFERENCE_REQUIRED` | Factor ID 或 Version 缺失 |
| `ALPHA_FACTOR_NOT_FOUND` | 精确 Factor 版本不存在 |
| `ALPHA_FACTOR_NOT_VALIDATED` | Factor 不是 VALIDATED |
| `ALPHA_FACTOR_NOT_ACCESSIBLE` | 当前 Research 无权引用该 Factor |
| `ALPHA_FACTOR_LIBRARY_REQUIRED` | Validate 前某个 Factor 尚不满足项目级 Library 依赖 |
| `ALPHA_WEIGHT_NON_FINITE` | Weight 为 NaN 或 Infinity |
| `ALPHA_TRANSFORM_UNSUPPORTED` | Transform 不在 Capability 中 |
| `ALPHA_COVERAGE_INVALID` | Coverage 不在 `(0, 1]` |
| `ALPHA_CROSS_SECTION_INVALID` | Minimum Instruments 小于 1 |
| `ALPHA_UNIVERSE_REQUIRED` | 当前 Research 无固定 Snapshot |
| `ALPHA_CROSS_SECTION_EXCEEDS_UNIVERSE` | Minimum Instruments 大于 Universe 成员数 |
| `ALPHA_DEPENDENCY_REQUIREMENT_INVALID` | 某 Factor Requirement 无法编译 |

### 8.2 Warnings

| Code | 条件 |
|---|---|
| `ALPHA_ALL_WEIGHTS_ZERO` | 所有 Weight 为 0 |
| `ALPHA_DUPLICATE_COMPONENT_REVIEW` | 同一 Factor、Transform 与方向被重复加入 |
| `ALPHA_RAW_SCALE_MIXED` | RAW 与 CS_RANK 混合，量纲可能不可比 |
| `ALPHA_NEGATIVE_WEIGHT_REVIEW` | 使用负权重，提醒确认含义 |
| `ALPHA_SINGLE_INSTRUMENT_RANK` | CS_RANK 但 Universe 只有一个成员 |
| `ALPHA_LOW_COVERAGE_REVIEW` | Minimum Coverage 低于推荐阈值 |

Warnings 不阻止 Preview。v1 明确把 `ALPHA_ALL_WEIGHTS_ZERO` 保持为 Warning，避免改变现有 AlphaEngine 语义。

## 9. Requirement 与数据闭包

Alpha 本身不直接新增 Bars 字段。它的数据需求是所有固定 Factor Requirement 的确定性并集。

编译流程：

1. 解析所有 Factor Definition ID + Version。
2. 验证 Factor 为 VALIDATED 且当前 Research 可访问。
3. 读取每个 Factor 的规范化 FactorSpec。
4. 使用当前 Universe Snapshot、Preview Start / End 构造 Context。
5. 把所有 FactorSpec 交给现有 RequirementCompiler。
6. 合并字段、频率、Provider、时间范围和每个 Input 的 Warmup。
7. 生成新的不可变 RequirementSet 版本或幂等复用完全相同的版本。
8. 使用 RequirementWorkspaceService 检查并准备数据。
9. 固定物理 Manifest 后才允许计算 Alpha Preview。

Requirement 的物理读取起点按各 Factor 的 required history 向前扩展；Alpha Preview 对外保存和展示的值仍严格裁剪到用户请求的 Start / End。Warmup 不足时返回数据缺口，不得通过缩短 Preview 输出范围静默通过。

如多个 Factor 使用不同频率，仍遵守各 Factor 已编译的显式时间对齐规则。Alpha 不隐式改变 Factor 的 PIT 或 alignment 语义。

## 10. Alpha Preview 流程

Alpha Preview 是 Definition Validation Evidence，不是 Formal Research Run。

```text
AlphaDraft
  + Dependency Closure
  + Universe Snapshot
  + RequirementSet
  + Physical Manifests
  + Preview Time Range
  + Factor/Alpha Engine Versions
→ compute Factor outputs
→ AlphaEngine.build_signals
→ persist AlphaPreview
```

### 10.1 计算步骤

1. 重新读取 Draft 并校验 expected fingerprint。
2. 服务端重新执行 Definition Checks。
3. 固定当前 Universe Snapshot 和 fingerprint。
4. 固定所有 Factor Definition、Version、Spec Hash 和 Code Hash。
5. 解析 RequirementSet 和精确 Manifest。
6. 按 Factor Engine 计算每个组件值。
7. 在每个 `as_of_time` 取所有组件共同拥有的 Instrument 交集。
8. 执行 `RAW` 或 `CS_RANK`。
9. 按 Weight 求 raw score。
10. 对 raw score 计算平均并列排名和 percentile。
11. `available_time` 取所有组件的最晚 available_time。
12. 计算 Coverage 和 Preview 分析。
13. 原子保存 AlphaPreview，并更新 AlphaDraft 的 latest preview 字段。

### 10.2 Preview 分析结果

```json
{
  "overall": {
    "time_points": 120,
    "mean_coverage": 0.94,
    "minimum_coverage": 0.8,
    "mean_raw_score": 0.51,
    "standard_deviation": 0.17,
    "excluded_instrument_count": 3
  },
  "latest_cross_section": [
    {
      "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
      "raw_score": 0.82,
      "rank": 1,
      "percentile": 1.0,
      "as_of_time": "...",
      "available_time": "..."
    }
  ],
  "latest_component_contributions": [
    {
      "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
      "factor_name": "momentum",
      "transformed_value": 0.9,
      "weight": 0.6,
      "contribution": 0.54
    }
  ],
  "exclusions": [
    {
      "instrument_id": "...",
      "missing_factors": ["volatility"]
    }
  ]
}
```

Alpha Preview v1 限制：

- 最大 31 天
- 最大 8 个 Factor 组件
- 最大结果行数
- 最新横截面只保存或返回有限数量用于默认展示
- 完整结果大小超过上限时返回稳定错误码

限制值优先与 Factor Preview 保持一致。

## 11. 指纹与失效规则

### 11.1 Draft Fingerprint

```text
sha256(
  alpha_draft schema version
  + canonical editor document
)
```

不包含：

- 更新时间
- UI 折叠状态
- 生成后的显示公式
- 临时错误文本

### 11.2 Dependency Fingerprint

```text
sha256(
  sorted [
    factor_definition_id,
    factor_version,
    factor_spec_hash,
    factor_engine_version,
    factor_code_hash
  ]
)
```

### 11.3 Preview Fingerprint

```text
sha256(
  alpha_preview schema version
  + draft_fingerprint
  + dependency_fingerprint
  + alpha_spec_hash
  + universe_snapshot_id
  + universe_fingerprint
  + preview start/end
  + sorted manifest IDs and content hashes
  + input bindings
  + factor engine versions/code hashes
  + alpha engine version/code hash
)
```

相同输入必须幂等复用相同 Preview；相同 fingerprint 不创建重复记录。

### 11.4 Validate 时重新检查

Validate 必须同时满足：

- Draft 仍为 `DRAFT`
- expected draft fingerprint 匹配
- Preview ID 存在并属于该 Draft
- Preview fingerprint 匹配
- Preview draft fingerprint 匹配当前 Draft
- Dependency fingerprint 重新计算后匹配
- Universe fingerprint 匹配
- Manifest 文件和内容 hash 仍有效
- Engine / Code Hash 匹配
- Preview 状态为 `READY`

任一条件失败，返回 `STALE` 类稳定错误，保留 Draft 和 Preview，不产生 Definition。

## 12. API 设计

所有本地 UI 写接口使用 `require_local_request`。

```text
GET    /api/research/alpha-drafts
POST   /api/research/alpha-drafts
POST   /api/research/alpha-drafts/validation
GET    /api/research/alpha-drafts/{draft_id}/validation
PUT    /api/research/alpha-drafts/{draft_id}
DELETE /api/research/alpha-drafts/{draft_id}

GET    /api/research/projects/{project_id}/alpha-factor-candidates
GET    /api/research/alpha-drafts/{draft_id}/preview-context
POST   /api/research/alpha-drafts/{draft_id}/requirements
POST   /api/research/alpha-drafts/{draft_id}/previews
GET    /api/research/alpha-drafts/{draft_id}/previews/latest
GET    /api/research/alpha-previews/{preview_id}

POST   /api/research/alpha-drafts/{draft_id}/validate
```

### 12.1 Create

```json
{
  "owner_project_id": "project_...",
  "library_scope": "PROJECT",
  "client_draft_key": "ui:project_...:session_uuid:editor_uuid",
  "document": {}
}
```

允许文档不完整。Research AlphaDraft API 只接受 `library_scope=PROJECT` 且必须提供 `owner_project_id`；提交 GLOBAL 返回 `ALPHA_LIBRARY_SCOPE_UNSUPPORTED`。GLOBAL Library authoring 不属于本编辑器。

### 12.2 Update

```json
{
  "expected_fingerprint": "...",
  "document": {}
}
```

错误：

```text
ALPHA_DRAFT_STALE
```

如规范内容变化，必须清空 Draft 上的 latest preview 引用，使 Preview 派生状态变为 `STALE`。

### 12.3 Discard

```json
{
  "expected_fingerprint": "..."
}
```

只把 Draft 状态改为 `DISCARDED`，不物理删除。

### 12.4 Preview

```json
{
  "expected_fingerprint": "...",
  "time_start": "...",
  "time_end": "...",
  "universe_snapshot_id": "..."
}
```

Universe Snapshot 必须与当前 Research 的固定 Primary Universe 一致。

### 12.5 Validate

```json
{
  "expected_fingerprint": "...",
  "preview_id": "alpha_preview_...",
  "preview_fingerprint": "..."
}
```

成功返回：

```json
{
  "draft": {},
  "definition": {},
  "library_asset": {},
  "project_reference": {}
}
```

最终 Definition：

- `definition_type=ALPHA`
- `state=VALIDATED`
- `library_scope=PROJECT`
- `owner_project_id=current Research`

Project Ref：

- Slot：`alpha:{name}`
- Mode：`PINNED`
- 精确 Definition ID + Version

Library 可见性遵循当前 Factor 的项目级行为，不把 Project Alpha 提升为 GLOBAL Definition。

## 13. 存储设计

本阶段新增平行表，不重构 Factor 表。

### 13.1 alpha_drafts

```sql
CREATE TABLE alpha_drafts (
  draft_id TEXT PRIMARY KEY,
  owner_project_id TEXT NOT NULL,
  library_scope TEXT NOT NULL DEFAULT 'PROJECT' CHECK (library_scope='PROJECT'),
  client_draft_key TEXT NOT NULL DEFAULT '',
  document_json TEXT NOT NULL DEFAULT '{}',
  draft_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT','VALIDATED','DISCARDED')),
  validated_definition_id TEXT NOT NULL DEFAULT '',
  latest_preview_id TEXT NOT NULL DEFAULT '',
  latest_preview_fingerprint TEXT NOT NULL DEFAULT '',
  previewed_draft_fingerprint TEXT NOT NULL DEFAULT '',
  previewed_at TEXT,
  created_by TEXT NOT NULL DEFAULT 'local_ui_user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  validated_at TEXT,
  FOREIGN KEY(owner_project_id) REFERENCES research_projects(project_id)
);
```

索引：

```text
(owner_project_id, state, updated_at)
UNIQUE (owner_project_id, client_draft_key) WHERE client_draft_key != ''
```

### 13.2 alpha_previews

```sql
CREATE TABLE alpha_previews (
  preview_id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  status TEXT NOT NULL,
  draft_fingerprint TEXT NOT NULL,
  dependency_fingerprint TEXT NOT NULL,
  preview_fingerprint TEXT NOT NULL UNIQUE,
  universe_snapshot_id TEXT NOT NULL,
  universe_fingerprint TEXT NOT NULL,
  requirement_set_id TEXT NOT NULL,
  time_start TEXT NOT NULL,
  time_end TEXT NOT NULL,
  factor_refs_json TEXT NOT NULL,
  manifest_ids_json TEXT NOT NULL,
  manifest_hashes_json TEXT NOT NULL,
  input_bindings_json TEXT NOT NULL,
  factor_engine_closure_json TEXT NOT NULL,
  alpha_engine_version TEXT NOT NULL,
  alpha_code_hash TEXT NOT NULL,
  spec_hash TEXT NOT NULL,
  values_json TEXT NOT NULL,
  analysis_json TEXT NOT NULL,
  diagnostics_json TEXT NOT NULL DEFAULT '[]',
  validated_definition_id TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT 'local_ui_user',
  created_at TEXT NOT NULL,
  FOREIGN KEY(draft_id) REFERENCES alpha_drafts(draft_id),
  FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
  FOREIGN KEY(universe_snapshot_id) REFERENCES universe_snapshots(universe_snapshot_id),
  FOREIGN KEY(requirement_set_id) REFERENCES requirement_sets(requirement_set_id)
);
```

索引：

```text
(draft_id, created_at DESC)
```

Alpha Preview v1 使用 `values_json`，并依靠 Capability 行数上限控制大小，与 Factor Preview 保持一致。改用 Artifact Storage 属于后续独立存储版本升级，不在 v1 中动态切换。

## 14. Capability Schema

在现有 `capabilities.alpha` 中增加：

```json
{
  "authoring_contract": {
    "document_version": "alpha_draft.v2",
    "input_model": "PINNED_FACTOR_LIST",
    "max_components": 8,
    "formula_model": "WEIGHTED_COMPONENT_SUM",
    "formula_source_editable": false,
    "supports_negative_weights": true,
    "supports_transforms": ["RAW", "CS_RANK"],
    "validation_flow": [
      "AUTO_BACKUP",
      "SAVE_DRAFT",
      "RUN_PREVIEW",
      "VALIDATE_ALPHA"
    ],
    "preview_role": "REQUIRED_VALIDATION_EVIDENCE",
    "preview_contract": "alpha_preview.v1"
  }
}
```

前端不得自行声明 Transform、Output Scale、Missing Policy 或 Rank Method。

## 15. 本地 UI 与 Agent API 边界

### 15.1 本地编辑器

新的 Alpha 编辑器只使用 `alpha-drafts` 和 `alpha-previews` 路径，不再通过通用 `/api/research/definitions` 创建 UI Alpha DRAFT。

### 15.2 Agent Research Session

现有受控 Agent 路径暂时保持兼容：

- 继续由 `/api/agent/*` 强制 Research Session、Capability 与 Scope。
- Agent 只传 `session_id`；不暴露内部授权兼容记录。
- 不扩大 allowed Provider、Universe、时间范围或预算。
- Agent 创建的 Alpha 仍是 PROJECT 范围。
- Agent 路径是否也强制 Alpha Preview，作为后续独立版本升级，不在本次 UI 同构中静默改变。

原因：Factor 当前也同时存在严格本地编辑器和 Session 控制的正式 Agent Definition 流程。本次只让 Alpha 本地编辑器与 Factor 本地编辑器一致，不破坏 Agent 自动化契约。

## 16. 安全与审计

所有 Alpha 写路径必须由后端检查：

- 当前 Project 存在
- Draft owner 与 Project 一致
- Project 私有 Factor 不可跨 Project 直接引用
- Library Factor 引用合法
- Validated Definition 不可覆盖
- expected fingerprint
- Preview current
- PIT 和 available_time
- Provider、Universe、时间范围
- Manifest 完整性
- Engine / Code Hash
- 审计写入成功

明确禁止：

- 扩大 Research Session Policy 或直接操作内部授权兼容记录
- 自动扩展范围或预算
- 自动发布 GLOBAL Definition
- 自动创建 Strategy
- Paper / Live
- 原始订单
- 直接业务数据库写入
- 删除历史事实

## 17. 兼容与迁移

### 17.1 已验证 Alpha

- 保持现有 `research_definitions` 记录不变。
- 保持现有 Project Ref、Library Asset、Run 和 Artifact 不变。
- 详情页继续按现有 AlphaSpec 展示。

### 17.2 基于旧 Alpha 创建新版本

转换规则：

```text
AlphaSpec.name                         → identity.name
AlphaSpec.version                      → 计算下一个语义版本
AlphaSpec.components                   → components
AlphaSpec.minimum_coverage             → advanced.minimum_coverage
AlphaSpec.minimum_cross_section_size   → advanced.minimum_cross_section_size
AlphaSpec.missing_policy               → advanced.missing_policy
AlphaSpec.rank_method                  → advanced.rank_method
AlphaSpec.output_scale                 → advanced.output_scale
```

转换后只创建 AlphaDraft，不创建新的 Definition，直到真实 Preview 和 Validate 成功。

### 17.3 Legacy DRAFT

当前没有 Legacy Alpha DRAFT。仍建议实现兼容分支：

- 如发现 `research_definitions.state=DRAFT` 的 PROJECT Alpha，提供“Convert to editable Draft”。
- 转换创建 AlphaDraft，并保留旧 Definition 作为历史记录。
- 不原地修改或删除旧 Definition。

## 18. 实现文件建议

新增：

```text
services/data_platform/alpha_draft.py
services/data_platform/alpha_preview.py
tests/unit/test_alpha_draft.py
tests/unit/test_alpha_preview.py
tests/unit/test_alpha_draft_api.py
tests/integration/test_alpha_preview_lifecycle.py
```

修改：

```text
services/data_platform/store.py
services/data_platform/definition_registry.py
services/data_platform/__init__.py
app.py
static/research_workspace_simple.js
static/research_workspace_simple.css
tests/unit/test_research_workspace_api.py
```

避免修改：

```text
FactorDraftService 的现有公开行为
FactorPreviewService 的现有公开行为
AlphaEngine 的评分语义
Formal Research Run / Frozen Bundle 合同
Strategy 与交易系统
```

允许抽取非常小的无语义公共工具，例如 canonical fingerprint 或 Preview 数字格式；不在本阶段把两套服务重构成复杂继承体系。

## 19. 实施顺序

### Phase A：契约与 Draft

1. 增加 Alpha authoring capability。
2. 增加迁移和 `alpha_drafts`。
3. 实现 AlphaDraftService。
4. 实现 Draft API。
5. 单元测试 fingerprint、并发、discard 和 compile。

### Phase B：Requirement 与 Preview

1. 增加 `alpha_previews`。
2. 实现 Factor dependency closure。
3. 编译所有 Factor 的 Requirement 并准备数据。
4. 计算 Factor outputs 和 Alpha signals。
5. 保存 Preview、分析和指纹。
6. 测试 stale、Manifest 损坏和幂等复用。

### Phase C：Validate 与 Library

1. Validate 强制当前 Preview。
2. 创建不可变 Alpha Definition。
3. 创建 `PINNED` Project Ref。
4. 保持 PROJECT 范围的 Library 可见性。
5. 验证旧 Run 和旧 Alpha 不受影响。

### Phase D：前端同构

1. Alpha 页面增加 Saved Work。
2. Alpha Draft 卡片与 Factor Draft 卡片同构。
3. Alpha 编辑器复用 Factor 的布局和 CSS。
4. 接入自动备份、Definition Checks、Preview 和 Validate。
5. 从 UI 移除通用 Definition DRAFT 创建路径。

### Phase E：回归与验收

1. Unit
2. API
3. Integration
4. Failure Injection
5. Research Workspace Frontend Contract
6. Browser read-only QA
7. Runtime smoke

## 20. 测试矩阵

### 20.1 Draft

- 不完整 Alpha 可保存。
- 同 fingerprint 更新幂等。
- expected fingerprint 错误返回 `ALPHA_DRAFT_STALE`。
- 内容变化清空 latest preview。
- Validated Draft 不可修改或丢弃。
- Discard 只改状态，不删除记录。

### 20.2 Definition Checks

- 无组件阻止 Preview。
- DRAFT Factor 阻止 Preview。
- Factor ID/Version 不匹配阻止 Preview。
- 私有跨 Project Factor 阻止 Preview。
- NaN / Infinity Weight 阻止 Preview。
- Unsupported Transform 阻止 Preview。
- Coverage 和 Minimum Instruments 范围检查。
- 负权重仅 Warning。
- RAW 与 CS_RANK 混合仅 Warning。
- RAW 禁用 Score Direction 并规范化为 `ascending=true`。
- CS_RANK 的高值高分/低值高分正确映射到 ascending。

### 20.3 Requirement

- 多 Factor 字段正确合并。
- 多频率使用已编译 alignment。
- Warmup 取每个 Factor 的确定性需求。
- 物理读取包含 Warmup，但 Preview 输出严格裁剪到请求范围。
- Warmup 不足返回缺口，不静默缩短输出。
- Requirement fingerprint 相同则幂等复用。
- Provider 或数据范围不可用时稳定阻塞。

### 20.4 Preview

- 真实多标的输出 raw score、rank、percentile。
- ties 使用 AVERAGE。
- `available_time` 使用组件最大值。
- Coverage 分母使用固定 Universe 成员数。
- 缺少任一组件的标的按 EXCLUDE 处理。
- 相同 Preview fingerprint 幂等复用。
- Factor 新版本、Universe、Manifest 或 Engine 变化后旧 Preview 失效。

### 20.5 Validate

- 无 Preview 不能 Validate。
- Stale Preview 不能 Validate。
- Draft/Preview fingerprint 不匹配不能 Validate。
- Factor 项目级 Library 依赖缺失时在创建 Definition 前阻止 Validate。
- 成功后生成不可变 VALIDATED Alpha。
- 成功后 Project Ref 为 PINNED。
- 成功后 Alpha 在项目级 Library 可见。
- 重试相同 Validate 不产生重复 Definition 或 Asset。
- Ref / Library finalization 暂时失败后，重试使用同一 Definition 完成。

### 20.6 UI

- Alpha 与 Factor 的主区块顺序一致。
- 自动备份状态可见。
- 字段错误正确高亮。
- Preview stale 后 Validate 立即禁用。
- 关闭再打开能恢复 Draft。
- 旧 Validated Alpha 可创建新版本 Draft。
- Alpha Preview 不创建 Run、Strategy 或交易对象。

## 21. 验收标准

只有以下条件全部满足才视为 Alpha 系统完成：

1. 用户可以保存不完整 Alpha 并在刷新后继续编辑。
2. Alpha 页面同时正确展示 Saved Work 和已验证定义。
3. 服务端检查是 Preview 与 Validate 的唯一权威。
4. 用户必须完成真实 Alpha Preview 才能 Validate。
5. Preview 展示真实横截面 score、rank、percentile 和 Coverage。
6. 任一依赖变化都会可靠地使 Preview 失效。
7. Validate 创建新不可变 Definition，不修改旧版本。
8. 新 Alpha 自动固定到当前 Research。
9. 项目级 Library 可见性与 Factor 一致，不提升到 GLOBAL。
10. Formal Run、Frozen Bundle、Artifact 和旧 Alpha 保持可复现。
11. Agent Grant、安全、PIT、Provider 和数据范围边界没有被绕过。
12. 不创建 Strategy，不触发 Paper 或 Live。

## 22. 最终产品语言

Factor 与 Alpha 对用户应呈现同一套心智模型：

```text
Define
→ Check
→ Preview with real data
→ Validate an immutable version
→ Test separately in Runs
```

两者的区别只在 Definition 内容：

```text
Factor = data variables + deterministic measurement formula
Alpha  = pinned Factors + deterministic cross-sectional scoring formula
```

这使 Research 工作流保持清晰：

```text
Universe
→ Factor
→ Alpha
→ Data
→ Factor / Alpha Test
```

Alpha 编辑与验证不会自动进入 Strategy，也不会执行虚拟或真实交易。

## 23. Factor / Alpha 同构约束

以下约束用于防止实现过程中只复制外观而遗漏生命周期。

| 能力 | Factor 基线 | Alpha 必须满足 | 允许的差异 |
|---|---|---|---|
| Saved Work | Factor Draft Card | Alpha Draft Card | 卡片摘要字段不同 |
| 自动检查 | 180ms debounce | 相同 | Alpha 检查组件依赖 |
| 自动备份 | 1400ms debounce + save queue | 相同 | Draft API 路径不同 |
| 并发控制 | expected draft fingerprint | 相同 | 错误码前缀为 ALPHA |
| 保存不完整定义 | 允许 | 允许 | Alpha 至少在 Preview 前需要 Factor |
| Preview 前置条件 | saved Draft + checks pass | 相同 | Alpha 还要解析 Factor closure |
| Requirement | Formula Input closure | Factor Requirement 并集 | 来源不同 |
| 真实值 Preview | 必须 | 必须 | 展示 score/rank/percentile |
| Preview 失效 | Draft/Universe/Manifest/Engine | 相同并增加 Factor dependency | 依赖集合不同 |
| Validate | current Preview required | 相同 | 生成 Alpha Definition |
| 不可变版本 | 必须 | 必须 | 无 |
| Project Ref | PINNED | PINNED | Slot 前缀不同 |
| Library 可见性 | PROJECT | PROJECT | Component Type 不同 |
| Test | Formal Factor Evaluation | Formal Alpha Evaluation | 指标不同 |

实现评审时，任何一行没有满足都视为未完成，不能以“Alpha 表单已经能保存”为验收依据。

## 24. 详细页面线框

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ RESEARCH · ALPHA                                      [× Close]             │
│ Research: BTC Cross Section  Version: 1.0.0  Engine: alpha-engine.v2       │
│ Draft State: Changes backed up · Preview not run                           │
├──────────────────────────────────────────┬──────────────────────────────────┤
│ IDENTITY                                 │ DEFINITION CHECKS                │
│ Name        [quality_momentum_alpha    ] │ Ready for Preview                │
│ Description [                           ] │ Errors 0 · Warnings 1           │
│                                          │ - Negative weight review         │
│ INPUT                                    ├──────────────────────────────────┤
│ momentum   [Momentum@1.0.0 VALIDATED]    │ VALUE PREVIEW                    │
│ volatility [Volatility@2.1.0 VALIDATED]  │ Universe: Crypto Top 5           │
│                         [+ Add Factor]    │ UTC Start [ ] End [ ]            │
│                                          │ Requirement: Ready               │
│ FORMULA                                  │                                  │
│ momentum   CS_RANK  High→High   0.6      │ Latest Cross Section             │
│ volatility CS_RANK  Low→High    0.4      │ BTC  0.82  Rank 1  Pctl 1.00    │
│                                          │ ETH  0.61  Rank 2  Pctl 0.80    │
│ alpha_score = 0.6*... + 0.4*...          │ ...                              │
│                                          │                                  │
│ OUTPUT                                   │ Coverage 100% · 5/5 Instruments │
│ Prediction Score · PERCENTILE            │ Fingerprint sha256...            │
│ Min Coverage [80%]  Min Instruments [3] │                                  │
├──────────────────────────────────────────┴──────────────────────────────────┤
│ Save Draft → Run Preview → Validate Alpha                                  │
│                              [Save Draft] [Run Preview] [Validate Alpha]    │
└─────────────────────────────────────────────────────────────────────────────┘
```

窄屏时右侧面板移动到 Definition 区下方，按钮顺序和状态不改变。

## 25. 操作状态矩阵

| 当前状态 | Save Draft | Run Preview | Validate Alpha | 说明 |
|---|---:|---:|---:|---|
| 新建、尚未保存 | 可用 | 禁用 | 禁用 | 自动备份会创建 Draft |
| Draft 已保存但有 Errors | 可用 | 禁用 | 禁用 | 保留不完整工作 |
| Draft 已保存且检查通过 | 可用 | 可用 | 禁用 | 等待真实 Preview |
| Preview Running | 可用 | 禁用 | 禁用 | 修改 Definition 会使本次结果过期 |
| Preview READY | 可用 | 可用 | 可用 | 允许重新 Preview |
| Draft 修改后 Preview STALE | 可用 | 可用 | 禁用 | 必须重新 Preview |
| Validate Running | 禁用 | 禁用 | 禁用 | 防止双击 |
| VALIDATED | 禁用 | 禁用 | 禁用 | 关闭编辑器并展示不可变卡片 |
| DISCARDED | 禁用 | 禁用 | 禁用 | 只保留历史事实 |

关闭编辑器时：

- 如果 save queue 为空，可直接关闭。
- 如果存在未完成保存，显示 `Finishing backup…`，等待当前请求结束。
- 如果保存失败，允许用户选择留在编辑器重试；不得静默丢失内容。
- 不提供“关闭且丢弃未保存内容”作为默认操作。

## 26. Factor 组件选择与版本规则

### 26.1 选择列表

每个选项显示：

```text
Factor Name @ Version
Source: Current Research / Library
Output: Unit · Direction
State: VALIDATED
```

选项由服务端返回的可引用集合驱动。前端不通过加载全部 Definitions 后自行猜测访问权限。

增加只读接口：

```text
GET /api/research/projects/{project_id}/alpha-factor-candidates
```

返回：

```json
{
  "project_id": "project_...",
  "candidate_fingerprint": "sha256...",
  "maximum_components": 8,
  "factors": [
    {
      "definition_id": "factor_...",
      "version": "1.0.0",
      "name": "momentum",
      "state": "VALIDATED",
      "spec_hash": "...",
      "engine_version": "factor-engine.v4",
      "output_unit": "RATIO",
      "output_direction": "HIGHER_IS_BETTER",
      "origin": "RESEARCH",
      "accessible": true
    }
  ],
  "diagnostics": []
}
```

Draft Definition Checks 必须重新验证候选集合；`candidate_fingerprint` 只用于 UI 刷新判断，不代替服务端授权。

### 26.2 已保存但当前不可用的 Factor

打开旧 Draft 或基于旧 Alpha 创建新版本时，如果某个 Factor 当前不可用于新研究：

- 保留原 ID 和 Version，不静默替换。
- 在 Input 卡片中显示 `UNAVAILABLE FACTOR`。
- 展示稳定原因码。
- 允许保存 Draft。
- 阻止 Preview 和 Validate。
- 用户必须显式选择替代 Factor 或移除组件。

### 26.3 Alpha 版本

- 新名称默认 `1.0.0`。
- 基于 Validated Alpha 创建新版本时默认 patch +1。
- 同名活动 Draft 也参与下一个版本计算。
- Name 和 Version 在首次保存后锁定；如需重命名，使用 `Copy as New Alpha`。
- 服务端是版本冲突的最终权威。
- Validate 时遇到不可变版本冲突返回 `ALPHA_VERSION_CONFLICT`，不得静默跳到下一版本。

## 27. Definition Preview 与 Alpha Test 的边界

两者必须在产品语言和数据对象上严格区分。

| 项目 | Alpha Definition Preview | Alpha Test |
|---|---|---|
| 目的 | 验证定义能否产生合理、可复现的真实值 | 评价 Alpha 的研究效果 |
| 入口 | Alpha 编辑器 | Research Runs |
| 时间范围 | 短范围，默认 7 天、最多 31 天 | 正式研究范围 |
| 输出 | score、rank、percentile、Coverage、贡献 | Rank IC、Spread、Stability、Turnover 等 |
| Requirement | 临时精确 Preview Requirement | Formal Run Requirement |
| Frozen Bundle | 不创建 | 创建 |
| Research Run | 不创建 | 创建 |
| Artifact | 不创建正式 Artifact | 创建不可变 Artifact |
| Grant Run Budget | 不占用 Formal Run 次数 | 按正式规则预留 |
| 可作为 Validate 证据 | 是 | 否；Test 发生在 Validate 之后 |
| Strategy / Trade | 不创建 | 不创建 |

Alpha 页面文案：

```text
Preview checks the definition with real data.
Alpha Test evaluates a validated Alpha in Runs.
```

不得把 Preview 的均值、标准差或最新排名描述为投资效果或策略结论。

## 28. API 响应与错误合同

### 28.1 成功响应

继续使用现有 Research API envelope：

```json
{
  "ok": true,
  "data": {}
}
```

### 28.2 错误响应

```json
{
  "ok": false,
  "error": "The Alpha Draft changed after this Preview.",
  "code": "ALPHA_PREVIEW_STALE",
  "diagnostics": [
    {
      "level": "ERROR",
      "code": "ALPHA_DEPENDENCY_FINGERPRINT_MISMATCH",
      "path": "components.1",
      "message": "Volatility@2.1.0 no longer matches the previewed dependency."
    }
  ]
}
```

HTTP 状态采用：

| Status | 使用场景 |
|---|---|
| 400 | 请求形状、字段或范围错误 |
| 404 | Draft、Preview、Factor、Project 不存在 |
| 409 | Fingerprint、Version 或并发冲突 |
| 422 | Definition 合法形状但当前无法 Preview / Validate |
| 500 | 未分类服务端错误 |

前端优先按 `code` 处理状态，`message` 只用于展示，不解析英文文本。

### 28.3 稳定 stale 错误

```text
ALPHA_DRAFT_STALE
ALPHA_PREVIEW_REQUIRED
ALPHA_PREVIEW_STALE
ALPHA_PREVIEW_FINGERPRINT_MISMATCH
ALPHA_DEPENDENCY_FINGERPRINT_MISMATCH
ALPHA_UNIVERSE_STALE
ALPHA_MANIFEST_STALE
ALPHA_ENGINE_STALE
```

任何 stale 错误都保留 Draft 和历史 Preview。

## 29. 自动备份、并发与幂等

### 29.1 前端节奏

与 Factor 保持一致：

```text
Definition Check debounce = 180ms
Automatic Backup debounce = 1400ms
```

- Definition Checks 使用递增 token，忽略晚到的旧响应。
- Draft 保存经过单一 `saveQueue` 串行执行。
- 只有最后一个已确认响应可以更新 current fingerprint。
- Preview 和 Validate 按钮在请求中禁用。
- 编辑内容变化时立即在本地把 Preview 标记为 STALE，不等待服务端返回。

### 29.2 Create Draft

前端在首次自动备份期间锁定重复 Create。Research UI 必须提交：

```text
client_draft_key = ui:{project_id}:{browser_session_uuid}:{editor_uuid}
```

相同 key 和相同初始内容幂等返回同一 Draft；相同 key、不同 Project 返回冲突。

### 29.3 Preview

Preview fingerprint 是天然幂等键：

- 相同 fingerprint 返回已有 Preview。
- 已有 Preview 为 READY 时不重复计算。
- 已有 Preview 为 FAILED 时，只有依赖或显式 retry token 变化才重新执行。

### 29.4 Validate

- 在创建 Definition 前预检所有 Factor 的项目级 Library 依赖和当前访问权限，避免可预见的半完成状态。
- 第一次成功后 Draft 记录 `validated_definition_id`。
- 重复提交相同 Draft/Preview 返回相同 Definition、Project Ref 和 Library 结果。
- 不产生第二个 Definition。
- 不重复提升 Library asset version。
- Definition 创建与 Draft 状态变化保持原子；Project Ref 和 PROJECT Library finalization 必须幂等。
- 如 Ref 或 Library finalization 遇到暂时性故障，重试使用同一 `validated_definition_id` 继续完成，不创建新版本。
- API 只有在 Definition、PINNED Ref 和 PROJECT Library 可见性全部完成后才返回整体成功。

## 30. 审计与可观测性

### 30.1 Authoring 事件

增加共享的 Research Authoring Event helper，并在同一版本应用到 Factor 和 Alpha，避免两者再次漂移。

事件：

```text
ALPHA_DRAFT_CREATED
ALPHA_DRAFT_UPDATED
ALPHA_DRAFT_DISCARDED
ALPHA_DEFINITION_CHECKED
ALPHA_REQUIREMENT_COMPILED
ALPHA_PREVIEW_CREATED
ALPHA_PREVIEW_REUSED
ALPHA_PREVIEW_FAILED
ALPHA_VALIDATED
ALPHA_PROJECT_REF_PINNED
ALPHA_PROJECT_LIBRARY_VISIBLE
```

事件只保存：

- actor type / ID
- project / draft / preview / definition IDs
- operation
- before / after fingerprint
- status / stable code
- duration
- created_at

不复制完整 Factor 值、Manifest 内容、配置密钥或敏感数据。

关键状态变更和审计事件必须在同一事务内成功；审计失败时写操作失败回滚。只读 Definition Checks 可以使用结构化日志，不要求写持久审计事件。

### 30.2 指标

```text
research_alpha_draft_create_total
research_alpha_draft_update_total
research_alpha_definition_check_duration_ms
research_alpha_preview_duration_ms
research_alpha_preview_reuse_total
research_alpha_preview_stale_total{reason}
research_alpha_validate_total{result}
research_alpha_preview_rows
research_alpha_preview_components
```

### 30.3 日志

每次 Preview 使用统一关联字段：

```text
request_id
project_id
draft_id
preview_id
draft_fingerprint_prefix
dependency_fingerprint_prefix
preview_fingerprint_prefix
```

日志不得输出完整 `values_json`。

## 31. 性能与资源限制

由 `capabilities.alpha.authoring_contract` 返回，不在前端硬编码：

```json
{
  "max_components": 8,
  "max_preview_days": 31,
  "default_preview_days": 7,
  "max_preview_value_rows": 20000,
  "max_latest_cross_section_rows": 200,
  "max_component_contribution_rows": 2000,
  "preview_timeout_seconds": 120
}
```

计数定义：

```text
preview_value_rows =
  successful_time_points × instruments_with_alpha_signal
```

超限时在计算前尽早拒绝：

```text
ALPHA_PREVIEW_RANGE_TOO_LARGE
ALPHA_PREVIEW_RESULT_TOO_LARGE
ALPHA_PREVIEW_COMPONENT_LIMIT
ALPHA_PREVIEW_TIMEOUT
```

Preview 分析还要记录被 AlphaEngine 跳过的候选时间点：

```json
{
  "candidate_time_points": 130,
  "successful_time_points": 120,
  "rejected_time_points": 10,
  "rejection_reasons": {
    "MINIMUM_COVERAGE": 7,
    "MINIMUM_CROSS_SECTION_SIZE": 3
  }
}
```

该诊断由 AlphaPreviewService 生成，不改变 AlphaEngine 的信号计算结果。

## 32. 发布、切换与回滚

### 32.1 Feature Flag

```text
research_alpha_draft_editor_v1
```

Flag 只控制 UI 入口，不控制数据库读取和后端兼容接口。

### 32.2 发布顺序

1. 发布数据库迁移和后端只读能力。
2. 发布 AlphaDraft API，Feature Flag 关闭。
3. 运行 Unit、API、Integration 和 failure injection。
4. 发布新前端，内部项目开启 Flag。
5. 完成无写 Browser QA。
6. 使用专门测试项目完成一次真实 Draft → Preview → Validate 验收。
7. 默认开启新编辑器。
8. 保留旧 Validated Alpha 的读取和新版本转换能力。

### 32.3 回滚

出现问题时：

- 关闭 Feature Flag。
- 不删除 AlphaDraft 或 AlphaPreview。
- 不把 AlphaDraft 自动转换成通用 DRAFT Definition。
- 已验证 Alpha、旧 Run 和 Artifact 不回滚。
- 新 Draft 在 UI 中显示“Editor temporarily unavailable”，并保留恢复入口。
- 修复后重新开启，新编辑器从原 Draft fingerprint 继续。

### 32.4 旧创建路径退役

新编辑器稳定后：

- UI 不再调用通用 `/api/research/definitions` 创建 Alpha DRAFT。
- 通用本地 Alpha DRAFT 创建路径标记为 Legacy。
- 不立即删除接口，避免破坏旧客户端。
- Agent `/api/agent/*` 合同保持独立，不受 UI Legacy 标记影响。

## 33. 最小可交付切片

为了尽早获得可验证结果，同时不交付半套生命周期，最小切片必须包含：

```text
AlphaDraft persistence
+ server Definition Checks
+ exact Factor dependency closure
+ real Alpha Preview
+ fingerprint-gated Validate
+ immutable Definition
+ PINNED Project Ref
+ PROJECT Library visibility
+ Saved Work UI
```

以下内容不能拆到“以后再补”，否则 Alpha 仍不等价于 Factor：

- Preview 强制验证
- stale 检查
- Draft 恢复
- 精确 Factor Version
- 不可变版本

以下内容可以在主切片之后追加，但不得改变合同：

- 更丰富的 Preview 图表
- Component contribution 可视化
- Preview 导出
- Draft 搜索与排序
- 公共 Factor/Alpha 编辑器组件重构
