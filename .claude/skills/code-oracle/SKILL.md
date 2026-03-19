---
name: code-oracle
description: "代码隐式知识提取与查询专家。为 AI 编码代理补全宏观认知盲区——
  特别是跨模块影响范围(blast_radius)、设计意图(rationale)、数据流拓扑(data_flow)。
  Use when: (1) 扫描模块提取隐式契约到知识图谱 (/code-oracle scan)
  (2) 编码前查询相关契约 (/code-oracle query)
  (3) 编码后验证是否违反契约 (/code-oracle verify)。
  输出: 仅注入 code_contracts 知识图谱。无需外部 API。"
---

# Code Oracle

> Fill AI coding agent macro blind spots | 5 contract types | 5 work modes | v4.1

## Core Rules (CRITICAL)

1. **禁止提取 AST/grep 能发现的知识** — 类继承、方法调用、字段引用都不是盲区
2. **禁止跳过 Round 3 (Devil's Advocate)** — 未经自我验证的契约不得注入 KG
3. **禁止忽略 blast_radius** — 每次 Module Scan 必须包含下游消费方分析
4. **禁止注入非默认 KG context** — 所有契约存默认 context (aim 知识图谱)
5. **禁止手写 confidence** — 由 Round 2 赋值、Round 3 校准

## 价值优先级 (基于 RED Phase 实测)

| 契约类型 | AI 自行推断能力 | code-oracle 价值 | 优先级 |
|----------|-----------------|-------------------|--------|
| `blast_radius` | **极弱** — 不追踪下游消费方 | 极高 | **P0** |
| `rationale` | **弱** — 只能猜测 | 高 | **P1** |
| `data_flow` | **中** — 不追踪完整链路 | 中 | **P2** |
| `ordering` | **强** — 可从代码推断 | 低-中 | P3 |
| `thread_safety` | **强** — 可从代码推断 | 低 | P3 |

**blast_radius + rationale 类型的契约数量不得少于总数的 50%。**

---

## Quick Decision Tree

```
我要做什么?
│
├─ 扫描模块，建立契约库
│   → Mode 1: Module Scan
│   → Round 0 (L3 Cross-Module) → Round 1-3 (LLM) → Pipeline
│
├─ 编码前查约束
│   → Mode 2: Contract Query
│   → aim_search_nodes (支持双向: involved + affected_external)
│
├─ 编码后验约束
│   → Mode 3: Change Verify
│   → git diff + 契约比对
│
├─ 检查契约是否过时
│   → Mode 4: Freshness Check
│   → 验证 involved_files 是否仍存在
│
└─ 增量同步 (git pull 后自动)        ← v4.1 新增
    → Mode 5: Incremental Sync
    → git diff + L3 影响链 → 报告 + Win 通知
```

---

## Mode 1: Module Scan (Build Library)

### Step 1: Source Preparation

```
小模块 (< 20 文件, < 100KB): 直接 concatenate
大模块 (>= 20 文件): 分层扫描
  Layer 1: 入口文件 + 基类 + 接口
  Layer 2: 按子系统分组 (3-5 文件/组)
  Layer 3: Layer 1+2 摘要 (跨组交互)
```

### Step 2: Four-Round Dialogue (Round 0-3)

使用 `references/prompt-templates.md` 中的完整 Prompt，按顺序执行:

**Round 0: L3 Cross-Module Discovery** (v4.0 重构)
- 读取 RepoMap L3 引用图 (`.claude/context/repomap-L3-relations.md`)
- 用 `repomap_bridge.py` 查询模块内类的所有外部消费者
- 输出: 外部消费者列表 (class -> consumer, relation_type)
- 注入 Round 1 上下文，替代 v2.0 的 Explore Agent 手动搜索
- **优势**: 秒级完成 vs Explore Agent 5 分钟，基于 AST 事实而非 LLM 猜测

**Round 1: Architect's Eye** — 理解模块全局架构
- 完整数据流拓扑
- 隐式逻辑依赖
- 生命周期窗口
- **必须回答: "谁消费了本模块的输出？"**
- **将 Round 0 的跨模块发现纳入分析**

**Round 2: Contract Mining** — 提取隐式契约
- 输出 JSON 数组
- 5 种类型: data_flow / ordering / rationale / thread_safety / blast_radius
- 每个契约: type, title(英文), description(中文), blind_spot(中文), violation_consequence(中文), involved_files, confidence
- **新增可选字段**: `affected_external_files` — 被本模块影响的外部文件 (用于双向查询)
- **blast_radius + rationale >= 50%**

**Round 3: Devil's Advocate** — 自我过滤
- 判断标准: "AI 读完 involved_files 后能否自行推断?"
- 能推断 → DROP
- 需要模块外文件 → KEEP
- 需要业务/历史背景 → KEEP
- 目标: 15-30 个契约

### Step 3: Pipeline Post-Processing

```bash
python scripts/pipeline.py --input round3.json --module-name <Name> --source-root <path> \
  --repomap-l3 .claude/context/repomap-L3-relations.md
```

六阶段 (v4.1):
0. **L3 Cross-Module Injection** (可选) — 用 RepoMap L3 自动补充 affected_external_files
1. Contract Validation — 格式 + 文件存在性
2. Semantic Dedup — Pass 1 (title difflib) + Pass 2 (cross-type difflib) + **Pass 3 (bge-m3 embedding, v4.1)**
3. Blind Spot Filter — 启发式过滤 AI 可自行推断的契约 (thread_safety 降级)
4. Stats + Quality Gate — 统计有效契约数 (conf > 0.5) + 质量门禁
5. KG Injection — 转换为 KG 注入格式

### Step 4: Verify

```
aim_search_nodes(query="<ModuleName>")
```

---

## Mode 2: Contract Query (Pre-Coding)

### Input
即将修改的文件名列表

### Flow
1. `aim_search_nodes(query="<filename>")`
2. **过滤 confidence <= 0.5 的契约** (被 Blind Spot Filter 降级的低价值契约)
3. 按 confidence 降序
4. 提取 Must-Read 文件列表 (从契约的 involved_files 中提取用户未提及的文件)
5. 格式化输出

### Output

```
修改这些文件前需要知道:

Must-Read (修改前建议先阅读):
  - OrderService.cs    ← 被 blast_radius 契约引用
  - InvoiceGenerator.cs        ← 被 blast_radius 契约引用
  - PaymentGateway.cs       ← 被 data_flow 契约引用

[blast_radius] ActorSyncSystem output affects all presentation layer (0.95)
  盲区: 修改同步逻辑时只看到 PaymentGateway 内部，不知道影响所有表现层
  后果: 血条不更新、位移错误、特效不触发

[rationale] ClearActorsData per-frame is intentional (0.88)
  盲区: 认为每帧清空重建低效，尝试改为增量模式
  后果: 残留数据导致已销毁对象仍被渲染
```

### Must-Read Generation Rules
1. 收集所有匹配契约的 `involved_files`
2. **排除**用户已知文件 (即输入的文件名列表)
3. 按被引用次数降序排列
4. 标注引用来源的契约类型

### No Match

```
未找到相关隐式契约。该模块可能尚未 scan。
建议: /code-oracle scan <module-path>
```

---

## Mode 3: Change Verify (Post-Coding)

### Flow
1. `git diff --name-only` 提取修改文件
2. 查询关联契约
3. LLM 判断 diff 是否违反契约 (Prompt 见 `references/prompt-templates.md`)

### Output

```
Code Oracle Verify Report:

  PASS      [ordering] System registration order (0.95)
            未修改注册顺序

  WARN      [data_flow] BulletsHpChangedDataArr pipe (0.90)
            新增了读取，请确认阶段正确

  VIOLATION [thread_safety] IJob constraint (0.92)
            改为 IJobParallelFor 会引发竞态
```

---

## Mode 4: Freshness Check (Staleness Detection)

### Purpose
定期检查已注入 KG 的契约是否仍然有效（引用的文件是否还在代码库中）。

### Flow

```bash
python scripts/freshness_checker.py \
  --input test/mgmultigate-round3-output.json \
  --source-root ./src/MyModule/ \
  --extended-root ./src/
```

### Output

```
=== Freshness Check Report ===
Total: 13 | FRESH: 12 | STALE: 1 | MISSING_ALL: 0

--- STALE (部分文件不存在，需更新) ---
  [blast_radius] Registry clear must precede level disposal (conf=0.92)
    involved_files 缺失: UIMultiGateHp.cs

=== Summary ===
  Freshness rate: 12/13 (92%)
  Action needed: 1 to update, 0 to delete
```

### When to Trigger
- 模块代码大重构后
- 每月定期检查
- 契约查询返回可疑结果时

---

## Mode 5: Incremental Sync

### Purpose
git pull 后自动检测已扫描模块的变更，报告受影响的契约和未覆盖的高影响文件。

### Trigger
- **自动**: post-merge git hook (与 RepoMap 共用 hook)
- **手动**: `python scripts/oracle_sync.py --l3 <l3_path> --report <report_path>`

### Flow
1. `git diff ORIG_HEAD HEAD` → 变更 .cs 文件列表
2. `incremental_scanner.py` → 匹配受影响契约 + L3 影响链
3. `freshness_checker.py` → 检测过时契约
4. 写入 `.claude/context/oracle-sync-report.json`
5. Windows toast 通知 (可选 `--notify`)

### New Scripts (v4.0-v4.1)

| 脚本 | 用途 |
|------|------|
| `repomap_bridge.py` | 解析 RepoMap L3 引用图，提供跨模块消费者查询 |
| `incremental_scanner.py` | git diff + L3 影响链分析 |
| `oracle_sync.py` | post-merge 编排: incremental + freshness + report + notify |

---

## Module Scan Priority

> 不是所有模块都值得 scan。扫描成本约 30-60 分钟/模块，应优先投入高回报模块。

### Priority Decision Tree

```
模块是否值得 scan?
│
├─ 跨模块耦合密度高? (输出被 >= 3 个外部模块消费)
│   → 高优先级 (blast_radius 契约价值极高)
│   → 示例: PaymentGateway (被表现层/UI/特效全链路消费)
│
├─ 技术复杂度高? (Jobs/ECS/多线程/异步状态机)
│   → 高优先级 (AI 推断困难，盲区密度大)
│   → 示例: GameplayModule (complex architecture + async patterns)
│
├─ 业务修改频率高? (近 3 个月 commit 频繁)
│   → 中优先级 (契约被消费概率高)
│   → 用 git log --since="3 months ago" --oneline -- <path> | wc -l 评估
│
├─ 模块封装良好? (接口清晰、内部复杂但外部简单)
│   → 低优先级 (AI 只需读接口即可正确使用)
│
└─ 纯 CRUD / 配置驱动?
    → 不 scan (ROI 极低)
```

### Complexity Score (5 Dimensions)

| 维度 | 低 (0-1) | 高 (2-3) | 权重 |
|------|----------|----------|------|
| 跨模块耦合 | 输出仅模块内消费 | 被 3+ 外部模块消费 | **×3** |
| 技术复杂度 | 普通 C# 类 | Jobs/ECS/多线程 | ×2 |
| 隐式状态 | 无状态或状态简单 | 多阶段生命周期窗口 | ×2 |
| 修改频率 | 近 3 月 < 10 commits | 近 3 月 > 30 commits | ×1 |
| 模块规模 | < 10 文件 | > 30 文件 | ×1 |

**评分 >= 15: 强烈建议 scan | 10-14: 建议 scan | < 10: 跳过**

### 实测基准 (基于已扫描模块)

| 模块 | 评分 | 契约数 | P0+P1 占比 | avg conf | 实际价值 |
|------|------|--------|-----------|----------|----------|
| PaymentGateway | ~21 | 16 (14有效) | 64% | 0.82 | 极高 — 下游消费方全盲 |
| OrderSystem (non-Gateway) | ~17 | 13 | 92% | 0.88 | 高 — 跨层交互密集 |

### Recommended scan order

基于 `.claude/rules/architecture.md` 的模块分析:

| 优先级 | 模块 | 理由 |
|--------|------|------|
| **已完成** | PaymentService/Gateway | multi-threading + full downstream consumption |
| **已完成** | OrderSystem (non-Gateway) | MRA + 跨层交互 |
| 下一个 | Battle/ | 战斗核心，跨模块耦合极高 |
| 下一个 | City/ | 城建核心，事件链复杂 |
| 中期 | Map/ | 大地图，异步加载 + 状态机 |
| 中期 | Activity/ | 活动系统，多态基类 + 配置驱动 |
| 低优先 | Chat/, Mail/ | 封装良好，接口简单 |

---

## Contract Data Format

```json
{
  "type": "blast_radius",
  "title": "ActorSyncSystem output affects all presentation layer",
  "description": "ActorSyncSystem.PreUpdate 从 NativeArray 同步到 C# 对象。所有表现层在 LateUpdate 后读取同步数据",
  "blind_spot": "AI 修改时只看到 PaymentGateway 内部文件，不知道输出被所有表现层消费",
  "violation_consequence": "表现层异常: 血条不更新、位移错误、特效不触发",
  "involved_files": ["DataSyncService.cs", "OrderService.cs", "InvoiceGenerator.cs"],
  "confidence": 0.95
}
```

**字段规则**:
- `title`: 英文 | `description/blind_spot/violation_consequence`: 中文
- `involved_files`: 文件名 (Pipeline 校验存在性)
- `confidence`: 0-1 (Round 2 赋值, Round 3 校准)

---

## Red Flags

| 红旗信号 | 正确做法 |
|----------|----------|
| 提取了大量 ordering/thread_safety 但没有 blast_radius | blast_radius 是核心，必须 >= 30% |
| 契约数量超过 40 | Round 3 过滤不充分 |
| involved_files 全在同一目录 | 缺少跨模块追踪 |
| "AI 读完代码就能推断"的契约被保留 | Round 3 应 DROP |
| 没跑 Pipeline 直接注入 KG | 必须走 Validation + Dedup |
| description 写成英文 | 语义字段必须中文 |

## Anti-Rationalization Table

| 借口 | 反驳 | 正确做法 |
|------|------|----------|
| "这个模块没有跨模块依赖" | RED Phase: AI 从不主动搜索模块外文件 | Round 1 明确追问下游消费方 |
| "ordering 也很重要" | RED Phase: AI 自己就能推断 | 降优先级，重点 blast_radius |
| "影响只有直接修改的文件" | RED Phase F4: AI 遗漏全部表现层 | 写入影响 + 读取影响 |
| "契约多=质量高" | 数量多=噪音高 | 控制 15-30 个 |
| "Round 3 太严格会漏" | 噪音比没有更糟 | 保持严格 |

## Common Errors

| 错误 | 症状 | 预防 |
|------|------|------|
| 跳过 Round 1 | 契约缺乏全局视野 | Round 1 不可跳过 |
| involved_files 幻觉 | Validation 报大量不存在 | 检查 LLM 是否编造文件名 |
| 全部是 ordering 类型 | 提取了 AST 能发现的 | 检查 blast_radius 比例 |
| Query 返回空 | 模块未 scan | 提示用户先 scan |
| Verify 全部 PASS | LLM 没认真比对 | 检查 diff 是否有实质变更 |

---

## Reference Files

| 文件 | 内容 | 何时读取 |
|------|------|---------|
| `references/contract-types.md` | 5 种契约类型定义 + 示例 | Module Scan 前 |
| `references/prompt-templates.md` | 4 轮 Prompt 完整模板 (含 Round 0) | 每次 Scan |
| `references/kg-schema.md` | KG 建模规范 | 调试 KG 注入 |
| `references/examples/sample-contracts.json` | PaymentGateway 示例契约 | 首次使用 |

## Design Documents

完整设计: `docs/plans/code-oracle-skill-plan.md`
RED Phase 观察: `.eval/red-phase-observations.md`
