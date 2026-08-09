# Research Alpha 编辑器实施蓝图

状态：待实施  
版本：v1  
日期：2026-07-31  
上位设计：[Research Alpha 编辑与验证系统设计](../03-features/research-alpha-editor-design.md)

> 2026-08-01 兼容说明：本文原有 `Project Research Grant` 表述描述当时的底层
> Formal Run 授权合同。当前用户与 Agent 工作流已经改为 Research Session；Agent
> 只传 `session_id`，后端内部授权记录继续为 Preview/Bundle/Run 提供兼容保护。

## 1. 实施目标

把当前 Alpha 的轻量通用 Definition 表单替换为与 Factor 同构的严格本地编辑流程：

```text
AlphaDraft
→ Definition Checks
→ Requirement
→ Real Preview
→ Fingerprint-gated Validate
→ Immutable Alpha Definition
→ PINNED Project Ref
→ PROJECT Library visibility
```

本蓝图只实现本地 Research Alpha 编辑器。现有 `/api/agent/*` Research Session 合同、Formal Run、Frozen Bundle、Artifact、Strategy、Paper 和 Live 均不改变。

## 2. 当前代码基线

截至 2026-07-31：

- Alpha UI 入口仍是 `static/research_workspace_simple.js` 中的 `alphaDialog`。
- Alpha 保存仍调用 `POST /api/research/definitions` 创建通用 DRAFT。
- Alpha 仍没有独立 Draft、Preview 或 fingerprint API。
- Factor 已有完整 `FactorDraftService`、`FactorPreviewService` 和 11 条本地 UI API。
- `DataPlatformStore` 当前最新 migration 为 18。
- Formal Research Run 已包含经过验证的 Factor Definition 执行和 AlphaEngine 调用。

实施时不得在未重新读取这些文件的情况下按本文行号机械修改；行号只用于本次设计定位，文件内容是最终权威。

## 3. 核心工程决策

### 3.1 保持 Factor 公共合同不变

以下公开行为不修改：

- Factor Draft API 路径
- Factor Draft/Preview JSON
- Factor editor DOM IDs 和用户流程
- Factor Preview fingerprint
- Factor Validate 行为
- Formal Run 输入和输出合同

允许做有 characterization test 保护的窄内部抽取。

### 3.2 AlphaDraft/AlphaPreview 使用平行服务

新增：

```text
AlphaDraftService
AlphaPreviewService
AlphaFactorCandidateResolver
```

不把 FactorDraftService 改造成复杂继承基类。两者共享的内容只抽取纯函数或无业务分支的底层执行器。

### 3.3 已验证 Factor 的计算以 Formal Run 为权威

Alpha Preview 的组件是已验证 Factor Definition，不是 Factor Draft。因此其 Factor 计算必须与 Formal Research Run 一致，而不是调用 FactorPreviewService 的 Draft 编译路径。

新增窄服务：

```text
FactorDefinitionExecutor
```

来源逻辑：

- `FormalResearchRunExecutor.execute` 中解析 Factor Definition 的分支
- `FormalResearchRunExecutor._bind_factor_inputs`
- legacy FactorSpec 与 FactorGraphSpec 的兼容分支

目标接口：

```python
class FactorDefinitionExecutor:
    def __init__(self, store: DataPlatformStore):
        ...

    def load_manifest_inputs(
        self,
        manifest_ids: Sequence[str],
        allowed_instruments: set[str],
    ) -> list[dict[str, Any]]:
        ...

    def execute_definition(
        self,
        definition: ResearchDefinition,
        manifest_inputs: Sequence[Mapping[str, Any]],
        allowed_instruments: set[str],
    ) -> tuple[FactorSpec | FactorGraphSpec, dict[str, list[dict[str, Any]]]]:
        ...
```

FormalResearchRunExecutor 改为调用该执行器；AlphaPreviewService 也调用同一执行器。

该抽取不得：

- 改变 Manifest 验证
- 改变 legacy Factor 行为
- 改变 FactorGraph input binding
- 改变 PIT、available_time 或排序
- 改变 Formal Run Artifact

抽取前先增加 characterization test，比较抽取前后的 Factor 输出和 Formal Run summary。

### 3.4 AlphaEngine 保持不变

Alpha Preview 直接使用现有：

```python
AlphaEngine().build_signals(
    spec,
    factor_outputs,
    universe_snapshot=snapshot,
)
```

额外的 rejected time point、component contribution 和 Preview summary 由 AlphaPreviewService 计算，不修改 AlphaEngine 信号语义。

## 4. 文件级改动地图

### 4.1 新增文件

| 文件 | 职责 |
|---|---|
| `services/data_platform/alpha_draft.py` | Draft persistence、检查、编译、Validate |
| `services/data_platform/alpha_preview.py` | Requirement、Manifest、真实 Preview、stale |
| `services/data_platform/alpha_factor_candidates.py` | 当前 Research 可引用 Factor 集合 |
| `services/data_platform/factor_definition_executor.py` | Formal Run 与 Alpha Preview 共用的已验证 Factor 执行 |
| `services/data_platform/research_authoring_audit.py` | Factor/Alpha 共享 authoring event |
| `tests/unit/test_alpha_draft.py` | Draft unit |
| `tests/unit/test_alpha_preview.py` | Preview unit |
| `tests/unit/test_alpha_draft_api.py` | Local API |
| `tests/unit/test_alpha_factor_candidates.py` | Candidate access |
| `tests/unit/test_factor_definition_executor.py` | 共享执行器 characterization |
| `tests/integration/test_alpha_preview_lifecycle.py` | 端到端 Draft→Preview→Validate |
| `tests/failure_injection/test_alpha_preview_failures.py` | stale、损坏、并发 |

### 4.2 修改文件

| 文件 | 修改 |
|---|---|
| `services/data_platform/store.py` | migration 19/20 |
| `services/data_platform/definition_registry.py` | Alpha authoring capability |
| `services/data_platform/factor_draft.py` | 在现有事务中写共享 authoring event，不改变 API |
| `services/data_platform/factor_preview.py` | 在现有事务中写共享 authoring event，不改变 API |
| `services/data_platform/research_run_service.py` | 使用 FactorDefinitionExecutor |
| `services/data_platform/__init__.py` | 导出新增类型 |
| `app.py` | AlphaDraft、Preview、Candidate API |
| `static/research_workspace_simple.js` | state、load、card、editor、actions |
| `static/research_workspace_simple.css` | 只补 Alpha 特有的小量样式 |
| `tests/unit/test_research_workspace_api.py` | HTML/JS/API contract |
| `tests/unit/test_research_run_contracts.py` | Formal Run 抽取无回归 |
| `.agents/skills/datatube/references/research-workspace-testing.md` | 如测试入口或计数发生变化则更新 |

### 4.3 明确不修改

```text
services/data_platform/factor_alpha.py 的 AlphaEngine 算法
FactorDraftService 公共方法
FactorPreviewService 公共方法
Research Run / Frozen Bundle schema
Agent Grant creation or mutation
Strategy / Approval / Order
```

## 5. 数据库迁移

### 5.1 Migration 19：Alpha authoring

一次迁移创建：

```text
alpha_drafts
alpha_previews
idx_alpha_drafts_project_state
idx_alpha_drafts_client_key
idx_alpha_previews_draft_created
```

字段以设计文档第 13 节为准。

迁移约束：

- `owner_project_id` 必须存在。
- AlphaDraft 仅允许 `library_scope=PROJECT`。
- `client_draft_key` 在同一 Project 内唯一。
- Preview fingerprint 全局唯一。
- Draft、Project、Universe Snapshot、RequirementSet 使用外键。
- migration 失败必须整体回滚。
- 不回填或修改现有 Validated Alpha。

### 5.2 Migration 20：Research authoring audit

新增共享表：

```sql
CREATE TABLE research_authoring_events (
  event_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  status TEXT NOT NULL,
  stable_code TEXT NOT NULL DEFAULT '',
  before_fingerprint TEXT NOT NULL DEFAULT '',
  after_fingerprint TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES research_projects(project_id)
);
```

索引：

```text
(project_id, created_at DESC)
(object_type, object_id, created_at DESC)
```

该表不保存完整 document、values 或 secret。

同一 release 中让 Factor 和 Alpha 的 create/update/discard/preview/validate 状态变更都写入该表。Factor 的 HTTP 和 JSON 合同保持不变。

### 5.3 Migration 测试

- 空库初始化直接得到 migration 20。
- migration 18 数据库可升级到 20。
- 重复启动幂等。
- migration 19 中途失败不残留半表。
- 旧 Definition、Run、Artifact 数量不变。
- 升级前数据库副本可恢复；不编写会删除历史对象的自动 down migration。

## 6. 服务接口

### 6.1 AlphaFactorCandidateResolver

```python
class AlphaFactorCandidateResolver:
    def __init__(self, store: DataPlatformStore):
        ...

    def resolve(self, project_id: str) -> dict[str, Any]:
        ...

    def assert_components_accessible(
        self,
        project_id: str,
        components: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        ...
```

`resolve` 返回：

- Project 自有 Validated Factor
- Project 当前 PINNED Library Factor
- 精确 ID、Version、Spec Hash
- origin、output unit/direction、engine version
- candidate fingerprint

不得返回：

- 其他 Project 私有 Factor
- DRAFT
- 不可用于新 Research 的 Superseded/Archived

`assert_components_accessible` 必须重新查 Registry 和 Project Ref，不能只相信 candidate fingerprint。

### 6.2 AlphaDraft 数据类

```python
@dataclass(frozen=True)
class AlphaDraft:
    draft_id: str
    owner_project_id: str
    library_scope: str
    client_draft_key: str
    document: dict[str, Any]
    draft_fingerprint: str
    state: str
    validated_definition_id: str
    latest_preview_id: str
    latest_preview_fingerprint: str
    previewed_draft_fingerprint: str
    created_by: str
    created_at: str
    updated_at: str
    previewed_at: str | None
    validated_at: str | None
```

### 6.3 AlphaDraftService

公开方法与 Factor 保持同形：

```python
class AlphaDraftService:
    @staticmethod
    def inspect_document(document: dict[str, Any]) -> dict[str, Any]:
        ...

    def inspect_project_document(
        self,
        document: dict[str, Any],
        owner_project_id: str,
    ) -> dict[str, Any]:
        ...

    @staticmethod
    def compile_document(
        document: dict[str, Any],
        *,
        universe_snapshot_id: str,
        resolved_factors: Sequence[ResearchDefinition],
    ) -> dict[str, Any]:
        ...

    def create(
        self,
        document: dict[str, Any],
        *,
        owner_project_id: str,
        client_draft_key: str,
        created_by: str = "local_ui_user",
    ) -> AlphaDraft:
        ...

    def update(
        self,
        draft_id: str,
        document: dict[str, Any],
        *,
        expected_fingerprint: str,
    ) -> AlphaDraft:
        ...

    def discard(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
    ) -> AlphaDraft:
        ...

    def inspect(self, draft_id: str) -> dict[str, Any]:
        ...

    def validate(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
        preview_id: str,
        preview_fingerprint: str,
    ) -> tuple[AlphaDraft, ResearchDefinition]:
        ...

    def get(self, draft_id: str) -> AlphaDraft | None:
        ...

    def list(
        self,
        *,
        owner_project_id: str = "",
        state: str = "",
        limit: int = 200,
    ) -> list[AlphaDraft]:
        ...
```

`inspect_document` 只检查结构和 capability。  
`inspect_project_document` 增加 Factor access、版本、Universe 和候选检查。  
`compile_document` 生成现有 AlphaSpec 兼容的 canonical dict。

#### 6.3.1 规范化规则

```text
Transform RAW
  → ascending=true
  → Score Direction 不参与 canonical fingerprint

Transform CS_RANK
  HIGH_VALUE_HIGH_SCORE → ascending=true
  LOW_VALUE_HIGH_SCORE  → ascending=false
```

变量名必须满足：

```text
^[A-Za-z_][A-Za-z0-9_]*$
```

组件按 UI 顺序保存，但 dependency fingerprint 按 Definition ID + Version 排序；交换仅用于展示的相同组件顺序不应改变 dependency identity，交换公式中不同组件的顺序可保持 Draft fingerprint 变化。

### 6.4 AlphaPreviewError

```python
class AlphaPreviewError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "preview",
    ):
        ...
```

与 FactorPreviewError 一样提供：

```json
{
  "diagnostics": [
    {
      "level": "ERROR",
      "code": "...",
      "path": "...",
      "message": "..."
    }
  ]
}
```

### 6.5 AlphaPreviewService

```python
class AlphaPreviewService:
    def context(self, draft_id: str) -> dict[str, Any]:
        ...

    def compile_requirements(
        self,
        draft_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def create(
        self,
        draft_id: str,
        payload: Mapping[str, Any],
        *,
        created_by: str = "local_ui_user",
    ) -> dict[str, Any]:
        ...

    def latest(self, draft_id: str) -> dict[str, Any] | None:
        ...

    def get(self, preview_id: str) -> dict[str, Any] | None:
        ...

    def assert_current(
        self,
        draft_id: str,
        *,
        preview_id: str,
        preview_fingerprint: str,
    ) -> dict[str, Any]:
        ...

    def mark_validated(
        self,
        preview_id: str,
        definition_id: str,
    ) -> None:
        ...
```

#### 6.5.1 compile_requirements

1. 校验 Draft fingerprint。
2. 解析 current Primary Universe Snapshot。
3. 解析所有精确 Factor Definition。
4. 取得 FactorSpec/FactorGraphSpec。
5. 把所有 Factor specs 加入 RequirementCompiler。
6. 合并当前项目已有非冲突来源。
7. 保存/复用 RequirementSet。
8. 更新本地 Requirement Ref。
9. 返回 evaluation range、required range 和 additional history。

不得：

- 把 Alpha 自己伪装成 Bars Requirement
- 丢失任一 Factor warmup
- 隐式改变频率 alignment
- 修改人工 Requirement

#### 6.5.2 create

1. 重复执行全部 Definition Checks。
2. 校验 expected fingerprint。
3. 校验时间范围和 capability limits。
4. 解析 RequirementSet。
5. 固定并验证 Manifest。
6. 使用 FactorDefinitionExecutor 计算所有 Factor。
7. 使用 AlphaEngine 计算 signals。
8. 对输出裁剪 requested range。
9. 生成 analysis。
10. 计算 fingerprint。
11. 相同 fingerprint 幂等复用。
12. 原子保存 Preview、更新 Draft latest preview、写 audit。

#### 6.5.3 assert_current

重新计算并校验：

- Draft fingerprint
- Factor dependency fingerprint
- Alpha spec hash
- Universe fingerprint
- RequirementSet
- Manifest hashes
- Factor engine/code closure
- Alpha engine/code hash

## 7. Validate finalization

### 7.1 可预见错误预检

创建 Definition 前必须完成：

- Preview current
- Factor access
- Factor 为 VALIDATED
- Factor 项目级 Library 依赖存在
- Alpha name/version 未冲突
- Project Ref slot 可写

任何预检失败不得创建 Definition。

### 7.2 Definition 与 Draft

AlphaDraftService.validate 在一个 DataPlatformStore transaction 内：

1. 创建或取得同一不可变 Alpha Definition。
2. 把 Draft 从 DRAFT 改为 VALIDATED。
3. 保存 validated_definition_id。
4. 标记 Preview validated_definition_id。
5. 写 authoring audit。

若 DefinitionRegistry 当前无法接收外部 connection，先增加最小 conn-aware private helper；公共 `DefinitionRegistry.create` 合同不改变。

### 7.3 Ref 与 Library

Definition transaction 成功后：

1. 幂等创建/确认 `alpha:{name}` PINNED Ref。
2. 幂等创建/确认 PROJECT Library visibility。
3. 写对应 audit event。

失败时：

- API 返回 stable error。
- Draft 保持 VALIDATED 并记录同一 Definition ID。
- 重试 validate 继续 finalization。
- 不创建第二个 Definition。
- UI 显示 `Finalizing Alpha…`，不能把结果当作完成。

只有 Definition、Ref、PROJECT Library 三者都完成才返回整体成功。

## 8. API 实施清单

按 `app.py` 的 Factor 路由旁边增加 Alpha 路由，保持相同错误处理形状。

| Method | Path | Service |
|---|---|---|
| GET | `/api/research/alpha-drafts` | `AlphaDraftService.list` |
| POST | `/api/research/alpha-drafts` | `AlphaDraftService.create` |
| POST | `/api/research/alpha-drafts/validation` | `inspect_project_document` |
| GET | `/api/research/alpha-drafts/{id}/validation` | `inspect` |
| PUT | `/api/research/alpha-drafts/{id}` | `update` |
| DELETE | `/api/research/alpha-drafts/{id}` | `discard` |
| GET | `/api/research/projects/{id}/alpha-factor-candidates` | `AlphaFactorCandidateResolver.resolve` |
| GET | `/api/research/alpha-drafts/{id}/preview-context` | `AlphaPreviewService.context` |
| POST | `/api/research/alpha-drafts/{id}/requirements` | `compile_requirements` |
| POST | `/api/research/alpha-drafts/{id}/previews` | `create` |
| GET | `/api/research/alpha-drafts/{id}/previews/latest` | `latest` |
| GET | `/api/research/alpha-previews/{id}` | `get` |
| POST | `/api/research/alpha-drafts/{id}/validate` | `validate + finalization` |

所有写路径：

- `require_local_request`
- `created_by=local_ui_user`
- 稳定 error code
- 不接受 GLOBAL scope
- 不创建 Agent Grant
- 不创建 Run 或 Strategy

## 9. 前端实施清单

### 9.1 State

增加：

```javascript
alphaDrafts: [],
alphaFactorCandidates: null,
```

增加 helper：

```javascript
function projectAlphaDrafts() { ... }
function alphaDraftFormula(document) { ... }
function alphaDraftCard(draft) { ... }
function normalizeAlphaDraftDocument(raw, version) { ... }
```

### 9.2 Load

全局和 Project load：

```text
GET /api/research/alpha-drafts
GET /api/research/alpha-drafts?owner_project_id={project}
GET /api/research/projects/{project}/alpha-factor-candidates
```

候选接口失败时：

- 页面仍可读取已验证 Alpha。
- Create Alpha 禁用。
- 显示稳定错误。
- 不回退到加载全部 Definitions 后自行拼候选。

### 9.3 Alpha 页面

`renderAlphas` 改为：

```text
Saved Alpha Drafts
+ Research Alpha refs
```

排序：

1. DRAFT 按 updated_at 降序
2. Validated refs 按 name/version

### 9.4 编辑器

用 `alphaDraftDialog` 替换旧 `alphaDialog` 的本地创建逻辑。

保留旧函数名作为短期 action adapter 可以接受：

```javascript
function alphaDialog(base = null) {
  return alphaDraftDialog(base);
}
```

编辑器应复用 Factor CSS：

```text
factor-editor
factor-editor-v3
factor-editor-main
factor-live-preview
factor-checks-panel
factor-preview-state
form-actions
```

Alpha 特有 class 使用 `alpha-` 前缀，不复制整套布局 CSS。

### 9.5 自动检查和备份

与 Factor 精确一致：

```text
validation debounce 180ms
backup debounce 1400ms
single saveQueue
validation token
dialogDestroyed guard
```

### 9.6 事件

新增 actions：

```text
edit-alpha-draft
discard-alpha-draft
add-alpha-component
remove-alpha-component
```

旧 actions：

```text
new-alpha
edit-local-alpha
```

继续有效，但转入 AlphaDraft editor。

### 9.7 Validate UX

状态：

```text
Checking
Needs attention
Ready for Preview
Running Preview
Preview ready
Preview stale
Finalizing Alpha
Alpha validated
```

请求中禁用重复操作。成功后：

1. 更新 state.alphaDrafts。
2. 更新 state.definitions / refs / library。
3. 关闭 Dialog。
4. 回到 Alpha tab。
5. 显示 validated card。

## 10. 测试实施顺序

### 10.1 WP0：Characterization

先添加、运行：

```powershell
python -m unittest tests.unit.test_factor_alpha_semantics
python -m unittest tests.unit.test_factor_preview
python -m unittest tests.unit.test_research_run_contracts
python -m unittest tests.integration.test_research_run_lifecycle
```

目标：共享执行器抽取前获得稳定基线。

### 10.2 WP1：Store + Capability + Shared Audit

测试：

```powershell
python -m unittest tests.unit.test_alpha_draft
python -m unittest tests.unit.test_research_workspace_api
```

完成条件：

- migration 20
- Alpha capability
- Factor 状态变更开始写共享 authoring event，但公共 API 不变
- 没有 Alpha API
- 现有测试全绿

### 10.3 WP2：Candidate + Draft

测试：

```powershell
python -m unittest tests.unit.test_alpha_factor_candidates
python -m unittest tests.unit.test_alpha_draft
python -m unittest tests.unit.test_alpha_draft_api
```

完成条件：

- 不完整 Draft 可保存
- stale update 冲突
- Project access 正确
- 无 Preview 时不可 Validate

### 10.4 WP3：FactorDefinitionExecutor

测试：

```powershell
python -m unittest tests.unit.test_factor_definition_executor
python -m unittest tests.unit.test_research_run_contracts
python -m unittest tests.integration.test_research_run_lifecycle
```

完成条件：

- v4 和 legacy Factor 输出不变
- Formal Run summary/artifact contract 不变

### 10.5 WP4：Requirement + Preview

测试：

```powershell
python -m unittest tests.unit.test_alpha_preview
python -m unittest tests.integration.test_alpha_preview_lifecycle
```

完成条件：

- 多 Factor Requirement 并集
- Warmup
- Manifest
- raw score/rank/percentile
- contribution/rejection summary
- fingerprint reuse/stale

### 10.6 WP5：Validate

测试：

```powershell
python -m unittest tests.unit.test_alpha_draft_api
python -m unittest tests.integration.test_alpha_preview_lifecycle
python -m unittest tests.failure_injection.test_alpha_preview_failures
```

完成条件：

- current Preview 强制
- immutable Definition
- PINNED Ref
- PROJECT Library
- retry finalization

### 10.7 WP6：UI

静态检查：

```powershell
node --check static/research_workspace_simple.js
python -m unittest tests.unit.test_research_workspace_api
```

完成条件：

- Saved Work
- 自动备份
- checks/preview/validate
- stale 按钮状态
- 旧 Validated Alpha 新版本转换

### 10.8 WP7：全量回归

```powershell
python ".agents/skills/datatube/scripts/research_workspace_test.py" --mode suite --repo "."
```

运行态只读检查：

```powershell
python ".agents/skills/datatube/scripts/research_workspace_test.py" --mode online --repo "."
```

全量：

```powershell
python ".agents/skills/datatube/scripts/research_workspace_test.py" --mode all --repo "."
```

## 11. Failure injection

必须覆盖：

| 场景 | 预期 |
|---|---|
| Draft 在 Preview 后变化 | `ALPHA_PREVIEW_STALE` |
| Factor spec hash 变化 | dependency mismatch |
| Factor 版本被替换 | dependency mismatch |
| Universe fingerprint 变化 | universe stale |
| Manifest 文件损坏 | manifest stale |
| Engine/code hash 变化 | engine stale |
| 两个并发 Draft update | 一个成功，一个 409 |
| 两个并发 Preview create | 幂等复用一个 Preview |
| 两个并发 Validate | 一个 Definition |
| Ref finalization 暂时失败 | retry 完成同一 Definition |
| Library finalization 暂时失败 | retry 完成同一 asset |
| Audit write 失败 | 状态变更回滚 |
| Preview 超时 | FAILED，Draft 保留 |

Failure injection 不清理生产历史，不使用真实交易接口。

## 12. 每个 Work Package 的提交边界

每个 WP 独立提交，便于回滚：

```text
WP0  tests: characterize validated factor execution
WP1  feat: add alpha authoring schema and capabilities
WP2  feat: add alpha factor candidates and draft lifecycle
WP3  refactor: share validated factor definition execution
WP4  feat: add alpha requirement and preview lifecycle
WP5  feat: gate alpha validation on current preview
WP6  feat: replace local alpha form with draft editor
WP7  test: complete alpha editor regression coverage
```

不要把 migration、全部后端、全部前端和测试压成一个不可分割提交。

## 13. 风险与处理

| 风险 | 处理 |
|---|---|
| Formal Run 抽取导致结果变化 | characterization + artifact contract test |
| Requirement 合并覆盖人工字段 | 沿用 compiler source groups，保留 MANUAL |
| Alpha Preview 太大 | capability limit + 计算前估算 |
| Legacy Factor 无法执行 | 保留 FactorSpec 分支 |
| Library finalization 半完成 | preflight + idempotent retry |
| 前端 autosave 乱序 | saveQueue + expected fingerprint |
| 旧 Alpha 新版本引用失效 Factor | 显示 unavailable，禁止 silent replacement |
| 用户把 Preview 当效果评估 | 文案和对象上与 Alpha Test 分离 |
| 新 UI 出错导致 Draft 不可见 | Feature Flag 关闭但保留 Draft |

## 14. 发布门槛

Feature Flag 默认开启前必须满足：

- migration upgrade 和备份恢复演练通过
- 新增 unit/integration/failure tests 全绿
- 现有 Factor tests 全绿
- Formal Research Run lifecycle 全绿
- JS syntax 和 Research workspace contract 全绿
- online GET-only checks 全绿
- 测试 Project 完成一次真实 Draft→Preview→Validate
- 验收前后现有 Run、Artifact、Definition 数量和 hash 无非预期变化
- 没有 Strategy、Approval、Order 或交易对象被创建

## 15. 实施完成报告模板

```text
目标
  Alpha local editor parity with Factor

完成状态
  WP0 ... WP7

新增对象
  migrations
  services
  endpoints
  tests

验证
  targeted unit
  integration
  failure injection
  research_workspace_test all

兼容
  existing validated Alpha
  Factor Draft/Preview
  Formal Research Run
  Agent API

未执行
  no Grant mutation
  no Strategy
  no Paper/Live

需要人工确认
  Feature Flag default enable
```

## 16. 开始编码前检查清单

- [ ] 重新读取 `git status`，保留用户已有改动。
- [ ] 确认设计文档 v1.1 仍是当前权威。
- [ ] 运行 WP0 characterization。
- [ ] 记录当前 migration 最大版本。
- [ ] 记录当前 Validated Factor/Alpha 数量。
- [ ] 记录当前 Formal Run 测试结果。
- [ ] 不扩大 Research Session Policy 或直接操作内部授权兼容记录。
- [ ] 不执行业务数据库手工写入。
- [ ] 所有代码编辑使用可审查补丁。
- [ ] 每个 WP 完成后立即运行对应测试。

实施完成前不得声称 Alpha 已与 Factor 等价。
