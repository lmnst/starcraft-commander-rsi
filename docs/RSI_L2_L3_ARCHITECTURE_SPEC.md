# 星际指挥台 RSI（L1–L3）架构与实施规范

> **文档用途**：面向总控编排 Agent（如 Astra）及下游编码模型的设计与施工规范。  
> **适用仓库**：`d:\xjzb\starcraft-commander-stage1-20261123`（基于 `v17` 指挥层）  
> **目标**：在不修改底层 C++ DLL / 冻结基线的前提下，用纯 Python 搭起 **L0（不可变底座）+ L1（战略）+ L2（观测与哨兵）+ L3（复合工具池 + 自动反射规则）** 的完整 RSI 双回路，支持实时原速对战、局间自主进化、安全校验回滚与进化时间线记录，并为后续无缝迁移至 **Windows OpenBW CLI（支持单机并行多局）** 及切换 **标准 LLM API** 预留接口边界。

---

## 1. 背景与必须解决的现状痛点

基于今日已跑通的 `app/stage2_live.py`（`matches/normal-codex/20261123-142700-941090`）实测结果：

1. **单层轮询空转严重**：`match-2` 耗时 70 分钟（61,502 帧），发起 176 次实时模型调用，其中 **165 次返回 `keep_current`**，真正触发原生按键仅 8 次；且第 42,240 帧起己方已 `workers=0, army=0`，仍空转轮询 20 余分钟直到人工授权 cutoff。
2. **只改纯文本 `strategy.md`（L1）难以落地**：`match-2` 复盘总结出“敌军靠近主力或基地时提前全军回防”，但只能写成散文塞入 `strategy.md`，无法沉淀为稳定的观测指标、复合动作或低延迟反射规则。
3. **缺少多局连续换版、离线校验与进化统计**：`stage2_live.py` 目前写死 2 局、仅第 1 局后可换版、缺少代码级门禁校验、未统一记录每局 token 消耗与“规则 vs. LLM”动作来源分布。

---

## 2. 不可违背的硬约束（执行前必读）

1. **禁止屏幕捕获**：严格遵守仓库根目录 `AGENTS.md`。测试和验证只读取日志、BWAPI 状态、JSON/JSONL 和回放元数据，不调用任何截图/录屏/CUA 工具；保持 `COMMANDER_ENABLE_SCREEN_CAPTURE=OFF`。
2. **禁止修改冻结底层**：不修改 `baseline/Bot.cpp`、`baseline/Baseline.dll`，本阶段**不修改也不重编** `app/CommanderLayer.cpp` 及其他 C++ DLL 源码。
3. **严格合法视野**：模型和可进化 Python 模块只能读取我方合法观测流（与 `stage2_live.py` 当前 `state` + `validation_native` 一致），严禁读取对手目录、对手源码、随机种子或开启全图信息。
4. **L0 隔离原则**：裁判逻辑、胜负/僵局判定、动作派发、AST 安全校验、回放门禁、版本切换（`active.json`）、批次统计全部属于 **L0 不可变底座**，位于模型可写边界之外。
5. **保持现有代码可用**：不破坏现有 `app/matches.py`、`app/stage1.py`、`app/stage2.py`、`app/stage2_live.py` 的兼容入口；新 RSI 架构建议新增独立模块目录（如 `app/rsi/`）和入口脚本（如 `app/rsi_live.py`）。

---

## 3. 目标系统架构

### 3.1 分层总览

```text
┌──────────────────── L0 不可变底座（app/rsi/，永不开放给模型写） ────────────────────┐
│  EngineAdapter (NativeV17 / 未来 Windows OpenBW CLI)                               │
│  合法观测采集 │ 原子键派发与回执核对 │ 僵局/超时截断 │ AST安全+离线回放校验 │ 版本/日志 │
└────────────▲──────────────────────────┬───────────────────────────▲────────────────┘
             │ (4) 执行原生键/回执核对   │ (1) 标准化 raw_obs 流     │ (5) 校验通过才升版
┌────────────┴──────────────────────────▼───────────────────────────┴────────────────┐
│                 Agent 可进化工作区（versions/v{N}/，局间慢回路可写）                   │
│                                                                                    │
│  [L1] strategy.md  —— 战略原则、对手打法记忆、决策偏好（供实时 LLM 阅读）              │
│  [L2] observer.py  —— 眼睛 summarize() + 哨兵 should_wake_llm()                    │
│  [L3a] tools.py    —— 基于 v17 10 键封装的复合工具池（步进式状态机）                   │
│  [L3b] rules.py    —— 自动反射规则 decide_reflex()（高频急救/行为蒸馏区）              │
└────────────▲──────────────────────────────────────────────────────▲────────────────┘
             │ 局内快回路：L2 摘要 → L3a 步进 → L3b 反射 → 哨兵按需唤醒实时 LLM          │
             └─────────────────── 局间慢回路：Evolution Agent 复盘与改写 ──────────────┘
```

### 3.2 批次与版本目录结构

每个批次目录（默认 `matches/rsi/<timestamp>/`）结构固定如下，每个 `v{N}` 均为自包含快照，天然支持回滚、固定版本对照和离线复测：

```text
matches/rsi/<batch_id>/
  batch.json                    # 批次总索引（参数、每局状态、耗时、token、胜负/截断、版本链路）
  active.json                   # 当前生效版本 {"version": "v1", "parent": null, "path": "versions/v1"}
  evolution-log.jsonl           # 进化时间线（每局追加：版本变更、修改文件/函数、蒸馏标记、报错、token）
  versions/
    v1/
      manifest.json             # {"version":"v1","parent":null,"created_after_match":0,"summary":"seed"}
      strategy.md               # [L1]
      observer.py               # [L2]
      tools.py                  # [L3a]
      rules.py                  # [L3b]
    v2/
      ...
  match-1/
    observations.json           # L0 归档的原始合法观测流（用于离线校验和离线决策对照）
    timeline.json               # 全量动作时间线（标注 source: rule / tool / llm + 原生回执）
    runtime-errors.json         # L2/L3 运行期异常与降级记录
    decision-001.json ...       # 实时 LLM 每次请求的完整 prompt/input/output/latency/tokens
    review-call-1.json ...      # 局间进化 Agent 请求存档
    review.json                 # 结构化复盘结论、证据、diff、校验结果、升版或回滚原因
    candidate-check.json        # 候选版本离线回放检验报告（含与父版触发次数对照）
```

---

## 4. 模块接口契约（核心施工标准）

### 4.1 基础原子原语（Base Primitives = v17 的 10+1 个按钮，后续可扩展）

L0 本阶段固定暴露以下原生按键语义，作为 L3 工具池和实时 LLM 的**初始原子动作集（Base）**；后续迁至 Windows OpenBW CLI 或扩展底层接口时，可在 `primitives.py` 中继续增补新的原子原语（如带坐标进攻/集结、建造/科技控制等），按钮总数不设死上限：

| Key | 语义名 | 行为说明（与 v17 `CommanderLayer` 一致） |
| --- | --- | --- |
| `1` | `rescue_probe` | 探机避险，解除原侦察指令干扰 |
| `2` | `lock_mining` | 强制采矿锁（保留采气与建造） |
| `3` | `select_squad` | 选小队（需 >=8 健康兵，抽约 1/4），其余主力锁定守家 |
| `4` | `select_all` | 下一次命令选择全军，已有锁防和小队任务继续 |
| `5` | `attack_nearby` | 进攻近处敌建筑或已知敌分矿。若回执是 `expansion_unknown`，原任务持续。可依战场证据选择 `6` 或 `-` |
| `6` | `clear_main` | 敌主基地区域清场（按当前所选范围）。实时模型须有 `enemy_main` 观测依据 |
| `7` | `guard_local` | 持续就近警戒，忽略探机，重复按更新位置 |
| `8` | `assemble_front` | 前线原地集结等援兵 |
| `9` | `retreat_safe` | 安全撤离并集结等候 |
| `0` | `defend_all` | 全军锁定守家，清除小队模式，保留采矿锁 |
| `-` | `restore_auto` | 解除手动任务与锁防，交回基线 Bot |

> **安全边界**：可进化代码（`observer.py` / `tools.py` / `rules.py`）**绝不能**直接发 Windows 消息或读写磁盘，只能返回纯字典/元组形式的动作意图，由 L0 校验后通过 `EngineAdapter.send_key(key)` 执行并核对原生回执。

### 4.2 标准化输入 `raw_obs`（由 L0 构造，完全对齐现有 v17 字段）

L0 复用 `stage2_live.py` 的日志解析逻辑，向 L2 传入只读的 `raw_obs` 字典，字段与实机 `native-start.json` / `observations.json` 一致：

- 基础标量：`frame`, `stance`, `mode`, `workers`, `completed_workers`, `army`, `main_army`, `healthy_army`, `center`, `main_task`, `squad_task`, `squad_formed`, `squad_ids`, `mining_lock`, `gathered_minerals`, `gathered_gas`, `scout`, `evade`
- 视野聚合：`own_unit_types`, `visible_enemies`, `visible_enemies_within_384px`（含 `own_bases` / `army_center` / `squad`）, `visible_undetected_enemies`, `own_base_positions`
- 逐单位合法明细：`units`（直接来自 `validation_native` 事件的己方单位与己方视野内可见敌方单位列表，含 `id`, `self`, `type`, `type_name`, `position`, `detected` 等合法字段，供 `observer.py` 后续自主迭代空间距离、部队分散度、阵地压力等高阶特征；注意 `summarize()` 输出仍受 <= 8KB 约束，不应把完整 `units` 列表原样塞给 LLM）
- 任务与近期事件：`tasks`（当前活跃 `battle_state`）, `recent_events`（近期 `TRANSITIONS` 事件列表）

---

### 4.3 `[L1] strategy.md`

- 纯文本/Markdown，长度上限 **2,000 字符**。
- 仅在调用实时 LLM（System 2）时作为当前战略上下文注入。
- `v1` 初始内容：保持简洁中立（沿用 `stage2_live.py` 的初始原则，不做过度人工战术灌输）。

---

### 4.4 `[L2] observer.py`

必须导出以下两个纯函数（不依赖外部 IO，不持全局隐式状态，跨帧记忆统一放在 L0 传入的 `memory: dict` 中）：

```python
def summarize(raw_obs: dict, memory: dict) -> dict:
    """
    将原始合法观测整理为结构化战场摘要（供 rules.py / tools.py / 实时 LLM 共用）。
    要求：
    - 返回 JSON 可序列化 dict，序列化后 <= 8192 bytes。
    - 必须保留基础键：frame, workers, army, healthy_army, mode, stance, squad_formed,
      mining_lock, visible_enemies, visible_enemies_within_384px, visible_undetected_enemies。
    - 允许计算衍生指标（如近 N 帧兵力变化、农民损失、威胁等级、基地受压标记等），
      需要跨帧追踪的中间数据存放在 memory dict 中。
    """

def should_wake_llm(summary: dict, prev_summary: dict | None, meta: dict) -> str | None:
    """
    哨兵函数：判断当前帧是否值得唤醒实时 LLM。
    meta 由 L0 提供，包含：
      - frames_since_last_llm: int | None
      - latest_receipt: dict | None
      - active_tool: str | None
      - recent_events: list[dict]
      - recent_decisions: list[dict]
    返回值：
      - 若需唤醒：返回非空短字符串原因（如 "opening", "base_under_attack", "army_drop"）
      - 若无需唤醒：返回 None
    """
```

- **`v1` 初始实现**：
  - `summarize`：透传核心状态并计算简单的 `workers_delta`、`healthy_army_delta`、`enemy_at_own_base`（bool）。
  - `should_wake_llm`：仅在 `opening`（`frame >= 120` 首次）、`contact_change`、`visible_enemy_change`、`own_force_change`、`task_or_base_event` 以及**较长的兜底周期**（建议 `PERIOD_FRAMES = 1800`，约 2 分钟，而非 720 帧）时唤醒，避免高频无意义 `keep_current`。

---

### 4.5 `[L3a] tools.py`（步进式状态机复合工具池）

**为什么必须采用步进式状态机而非阻塞循环？**  
禁止在工具内 `time.sleep` 或启线程。步进式状态机每来一帧执行一步（毫秒级返回），既绝不阻塞主循环，又能被 L0 拿历史 `observations.json` 在 0.1 秒内完成离线全轨迹回放测试！

必须导出 `TOOLS` 注册表，每项为一个工具描述 + `step` 函数：

```python
TOOLS = {
    "squad_attack_nearby": {
        "description": "Select squad (key 3), wait until mode=='squad', then attack nearby enemy/expansion (key 5).",
        "args_schema": {},  # 可选参数说明，如 {"retreat_army_floor": "int, optional"}
        "step": squad_attack_nearby_step,
    },
    ...
}

def squad_attack_nearby_step(summary: dict, args: dict, state: dict) -> dict:
    """
    每次收到新观测帧时调用一次。state 为本次工具运行的专属可变字典（由 L0 在启动时初始化为 {}）。
    必须返回以下 4 种结构之一：
      {"status": "emit_key", "key": "3", "reason": "request squad mode"}
      {"status": "wait", "reason": "waiting for mode==squad"}
      {"status": "done", "reason": "squad attack command dispatched"}
      {"status": "abort", "reason": "not enough healthy army (<8)"}
    """
```

- **L0 对工具运行的硬保护**：
  - 同一工具单次执行有最大存活帧数（如 `MAX_TOOL_FRAMES = 960`）与最大发键数（如 `MAX_TOOL_KEYS = 4`），超限自动 `abort` 并记日志，防止死循环发键。
  - 若 `rules.py` 或新一次实时 LLM 决策下达了新动作，L0 会自动中断旧 `active_tool`（记 `aborted_by_override`）再切新动作。
- **`v1` 初始种子工具**：
  - 提供 1～2 个最基础、已验证有效的示例工具（如 `squad_attack_nearby`：`3` → 确认 `mode == "squad"` → `5`；以及 `squad_assemble_front`：`3` → 确认 `mode == "squad"` → `8`），直接示范有状态多步按键怎么写。

---

### 4.6 `[L3b] rules.py`（自动反射规则 / 行为蒸馏区）

```python
def decide_reflex(summary: dict, active_tool: str | None, memory: dict) -> dict | None:
    """
    高频快反射钩子（每帧 state 更新时在 L0 中运行，0 API 延迟 / 0 Token）。
    不触发时返回 None。
    触发时返回：
      {
        "rule_name": "short_snake_case_name",
        "action_type": "key" | "tool",
        "target": "0" | "<tool_name>",
        "args": {},          # action_type == "tool" 时有效
        "reason": "short evidence-based reason"
      }
    """
```

- **L0 对自动反射的硬保护**：
  - 反射冷却间隔：`REFLEX_COOLDOWN_FRAMES = 120`（防止同一规则连续多帧重复狂按同一键）；
  - 同一动作防抖：若刚刚已收到相同键的回执且局势未变，L0 可跳过重复发送并记 `deduped`；
  - **`v1` 初始实现**：空壳（直接 `return None`），把空间留给模型从真实对局经验中自主蒸馏出第一批规则！

---

## 5. 局内执行主循环（快回路）设计

### 5.1 EngineAdapter 抽象（为后续 Windows OpenBW CLI 及并行多局铺路）

将引擎交互收口到极简接口（`app/rsi/adapter.py`）：
- `NativeV17Adapter`（本阶段实现）：复用 `app/matches.py` 的 `playing(..., visible_telemetry=True, data_only=True, keep_results=False)`、`stage1.py` 的 `game_window` + `post_key`、以及 `commander-layer.jsonl` 读取逻辑（单机串行跑局）。
- 未来切 **Windows OpenBW CLI**（复现并扩展 `starcraft-cli-lab-20261116` 的 Windows 无头 OpenBW 引擎）时，仅需新增 `OpenBWCliAdapter`（文件状态流 + 文件指令 + 可选引擎停帧 + 多进程隔离目录并发），L1–L3 和进化逻辑**一行不用改**。

### 5.2 单帧处理顺序（优先级从高到低）

在 `LiveRunner` 收到每一帧新的 `state` 时：

1. **僵局与超时检测（L0）**：
   - `--speed` 默认 42 ms/帧，`--max-frames` 默认 36000 帧，`--match-timeout` 默认开战后 1800 秒。墙钟独立于新观测推进，两个时限均请求原生 `leaveGame` 并等待回放。记录触发帧与实际结束帧。
   - `--stalemate-zero-frames` 默认 1800。连续观测为零工零兵达此帧数时，按 `stalemate_zero_units` 截断并记录 `adjudicated_outcome="cutoff_loss"`。帧数或墙钟超时保留具体原因，由下述规则独立裁定胜负。两类截断的 `natural_outcome` 均为 `unknown`。
   - 超时判胜仅适用于 Destination 对 PylonPuller。根据公开双出生点和开局唯一己方完工 Nexus 确定敌主矿位置。终局 120 帧内的最新合法观测中，敌主矿 96 像素内须有唯一可见、已完工、可攻击的敌 Nexus，且其血盾总和至多 200。相邻两次观测相隔最多 120 帧，须观测到同一个 Nexus 的血盾减少至少 32，当下至少两名 224 像素内的己方机动战斗单位正以它为目标，其中至少一名正在攻击或地面武器处于冷却。末次观测距截断请求不能超过一个观测周期加 1 秒，且当前视野出现其他已完工敌 Nexus 时按证据不足处理。满足以上条件记 `cutoff_win`，其他超时记 `cutoff_loss`。
   - 手动停批或模型服务熔断均通过原生离局保存轨迹和回放，`state` 分别为 `manual_stop` 和 `model_circuit_open`，保留自然结果未知和无裁定结果。
2. **更新观测摘要（L2）**：
   - 在 `try/except` 保护下调用 `observer.summarize(raw_obs, obs_memory)`。
   - 若抛异常或超限（>8KB/非 JSON），记入 `runtime-errors.json`，自动降级为 L0 内置的 `fallback_summarize(raw_obs)`，保证对局不崩。
3. **检查异步 LLM 回复（若有在途请求完成）**：
   - 解析 JSON：`{"action_type": "key" | "tool" | "keep_current", "target": "...", "args": {}, "reason": "..."}`。
   - 检查实耗墙钟或帧龄是否超过 24 秒预算。帧龄上限是 `floor(24000 / speed_ms_per_frame)`，默认速度对应 571 帧。未过期则：
     - `keep_current`：记 `kept_without_key`；
     - `key`：发送原生按键并等待回执，记 `source="llm"`；
     - `tool`：校验工具名在 `TOOLS` 中，激活 `active_tool`（记 `source="llm_tool:<name>"`），立即步进一次。
4. **步进当前 `active_tool`（L3a）**：
   - 若存在 `active_tool` 且未在等待原生回执，调用 `tool.step(summary, args, tool_state)`。
   - 若返回 `emit_key`：经 L0 校验后发键并记 `source="tool:<name>"`；若返回 `done` / `abort`：清空 `active_tool` 并记原因。
5. **运行自动反射规则（L3b）**：
   - 若未等待回执且不在反射冷却期内，调用 `rules.decide_reflex(summary, active_tool_name, reflex_memory)`。
   - 命中时（可覆盖当前 `active_tool`）：直接执行对应 `key` 或激活对应 `tool`，在 `timeline.json` 中标记 `source="rule:<rule_name>"`。
6. **哨兵唤醒实时 LLM（L2 → System 2）**：
   - 仅当 **无在途 LLM 请求** 且 **无等待回执** 且 **未刚触发反射** 时，调用 `observer.should_wake_llm(summary, prev_summary, meta)`。
   - 若返回非空 `trigger` 字符串，则在单线程 `ThreadPoolExecutor(1)` 中异步发起实时模型调用（单局同时最多 1 个在途请求）。

### 5.3 实时 LLM 决策接口约定

- **模型可配置**：支持 `--live-model`（例如便宜模型 `gpt-6-luna` via `codex exec`，或 OpenAI 兼容 HTTP endpoint），支持 `--reasoning-effort low`。
- **实时模型输入 JSON**：
  ```json
  {
    "policy_version": "v2",
    "strategy": "<strategy.md 全文>",
    "summary": { "...来自 observer.summarize..." },
    "trigger": "base_under_attack",
    "available_keys": { "1": "...", "...": "..." },
    "available_tools": { "squad_attack_nearby": { "description": "...", "args_schema": {} } },
    "active_tool": null,
    "recent_decisions": [ "...最近 5 条（含 rule/tool/llm 来源）..." ],
    "latest_receipt": { "..." }
  }
  ```
- **实时模型严格输出 JSON**（禁止调用任何 CLI 工具）：
  ```json
  {
    "action_type": "key | tool | keep_current",
    "target": "0 | squad_attack_nearby | null",
    "args": {},
    "reason": "short observation-based reason"
  }
  ```

---

## 6. 局间进化回路（慢回路）与安全校验门禁

### 6.1 局间进化 Agent（Evolution Agent —— 原生兼容“单局/多局一轮”）

每轮对局结束（自然结束或僵局截断）后，L0 汇总本轮证据并调用进化入口 **`review_round(matches: list[MatchSummary], ...)`**（可通过 `--review-model` 和 `--backend codex|openai` 单独指定，当前暂用 `codex` 后端，后续拿到 API Key 直接切 `--backend openai`，业务代码零修改）：
- **当前单局串行阶段**：每轮传 1 局（`len(matches) == 1`，即打 1 局复盘 1 次）；
- **后续 Windows OpenBW CLI 并行阶段**：每轮并行跑 2～3 局（约 20 分钟内跑完），汇总 `len(matches) == 2..3` 一起复盘思考并迭代 1 个新版本，接口无需重构。

- **输入包**：
  1. 本轮各局结果与统计（自然胜负或 `cutoff`、终局帧数、耗时、决策来源统计：`rule` 次数 / `tool` 次数 / `llm` 次数 / `keep_current` 次数 / 过期数 / 失败数）；
  2. 编号事实表 `facts`（沿用已验证有效的整数 `id` 采样方式：等距采样状态帧 + 关键峰值帧 + 每次执行前后状态 + 所有原生回执 + 终局事件）；
  3. 本局 `runtime-errors.json`（若有模块抛错，必须暴露给进化 Agent 以便修 Bug）；
  4. 上一轮留下的 `hypothesis` / `expectation`；
  5. 当前版本全部 4 个文件的完整源码（`strategy.md`、`observer.py`、`tools.py`、`rules.py`）。

- **防乱约束（写进 Evolution Prompt）**：
  - 每次复盘只聚焦 **1 个证据最充分的问题**；
  - 优先思考是否能将反复出现或对延迟敏感的判断**从文字蒸馏进 `observer.py` / `tools.py` / `rules.py`**，或修复已有代码 Bug；
  - **单次最多修改 2 个文件**（从 `strategy.md`, `observer.py`, `tools.py`, `rules.py` 中任选）；
  - 若证据不足以支持明确修改，选择 `keep`；若新版退化或报错频发，可选择 `revert` 回退至 `parent` 版本。

- **严格输出 JSON Schema**：
  ```json
  {
    "prediction_check": "对比上一轮 expectation 与本局事实（首局写 N/A）",
    "evidence": [12, 18],
    "diagnosis": "定位本局核心问题（引用事实中的具体数值和帧号）",
    "hypothesis": "预期改动为何能改善该问题",
    "expectation": "下一局可量化的检验指标",
    "decision": "keep | modify | revert",
    "distillation_type": "none | strategy_only | add_observation | add_tool | distill_rule | bugfix_code",
    "edits": {
      "rules.py": "修改后的完整文件内容（未修改的文件不要出现）"
    }
  }
  ```

> **为什么输出完整文件而不是 diff 补丁？**  
> 4 个文件本身都短小精悍（每个几十到百余行），让便宜模型输出完整文件比输出 unified diff 的成功率高得多，由 L0 用 `difflib.unified_diff` 自动计算并存档精确 diff。

### 6.2 L0 安全与功能校验门禁（`app/rsi/validator.py`）

进化 Agent 提出 `modify` 后，**必须连续通过以下 4 道硬门禁**，才允许生成 `v{N+1}` 并切换 `active.json`；任何一道失败均拒收、记录 `validation_error` 到 `review.json` 和 `evolution-log.jsonl`，下局继续沿用当前版本：

1. **门禁 1：白名单与体量检查**
   - `edits` 的键必须是 `{"strategy.md", "observer.py", "tools.py", "rules.py"}` 的非空子集，且 `1 <= len(edits) <= 2`；
   - `strategy.md <= 2000 chars`，单个 `.py` 文件 `<= 12000 chars`；
   - `evidence` 必须是 `1..3` 个存在于 `facts` 中的合法整数 ID。

2. **门禁 2：Python AST 静态安全检查（零妥协）**
   - 解析每个被修改 `.py` 文件的 AST：
     - **允许导入的标准库白名单**：`{"math", "collections", "statistics", "itertools", "functools", "copy", "typing", "dataclasses"}`；禁止任何其他 `import` / `from ... import`。
     - **禁止危险调用与属性访问**：禁止 `open`, `eval`, `exec`, `compile`, `__import__`, `getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `input`, `breakpoint`；禁止访问双下划线魔术属性（如 `__builtins__`, `__subclasses__`, `__class__`, `__dict__`, `__globals__`）。
     - **必须存在约定导出符号**：
       - `observer.py`：`summarize`, `should_wake_llm`；
       - `tools.py`：`TOOLS`（且每个条目的 `step` 为可调用对象）；
       - `rules.py`：`decide_reflex`。

3. **门禁 3：离线轨迹回放冒烟测试（Shadow Replay）**
   - 在受限执行环境（清空危险 builtins，单次执行总时限 <= 3 秒）中加载候选版本模块；
   - 读取刚结束这局的 `observations.json`（全量或最多等距取 200 帧）逐帧回放：
     - 校验每帧 `summarize()` 返回合规 dict、序列化 <= 8KB、必含基础字段；
     - 校验每帧 `should_wake_llm()` 返回 `None` 或短字符串；
     - 校验每帧 `decide_reflex()` 返回 `None` 或合法动作 dict（`key` 必须在 11 个合法键内，`tool` 必须在候选 `TOOLS` 内）；
     - 若 `tools.py` 有新增或修改工具，对每个工具至少构造 3 帧采样执行 `step()` 冒烟，验证返回状态属于 `{"emit_key", "wait", "done", "abort"}` 且发出的键合法；
     - **顺手统计行为对照指标**并写入 `candidate-check.json`：
       - 父版 vs. 候选版在同一份 `observations.json` 上的：`wake_llm_count`、`reflex_trigger_count`、具体触发帧差异列表。**这直接给周二验收提供了“代码改动确实改变行为”的客观离线证据！**

4. **门禁 4：语法/契约自检与原子落盘**
   - 生成 `versions/v{N+1}/` 全部 4 个文件（未改动文件从父版本完整拷贝）+ `manifest.json`；
   - 更新 `active.json`；
   - 向 `evolution-log.jsonl` 追加一行标准事件记录（含时间戳、局号、父/子版本、改动文件、AST 提取的改动函数名列表、`distillation_type`、验证状态、本局 token 汇总与决策来源统计）。

---

## 7. Token、延迟与进化可视化数据规范

为了直接支撑《星露谷》风格的进化时间线与 200 局预算估算，以下数据必须自动落盘：

1. **每次模型调用**（实时与复盘）：统一解析并存储 `input_tokens`、`output_tokens`、`cached_input_tokens`（若有）、`seconds`、`model`。
   - 对 `codex exec --json`：从 `type == "turn.completed"` 事件的 `usage` 字段提取（若无则记 `null` 并在摘要注明）；
   - 对 OpenAI 兼容 HTTP API：从响应体 `usage` 字段提取。
2. **每局 `batch.json` 记录**：
   - `decision_sources`: `{"rule": int, "tool_step": int, "llm_key": int, "llm_tool": int, "llm_keep_current": int, "expired": int, "invalid": int}`
   - `tokens`: `{"live_input": int, "live_output": int, "review_input": int, "review_output": int}`
   - `runtime_error_count`: `int`
   - `lock_defense` 记录主力 `stance == "defend"` 的累计帧、首条有效状态到实际结束的总帧数、两者的比值和状态变化的来源。原生键回执用于定位状态变化的具体帧。
   - `army_peaks` 记录全军与健康全军在合法观测中的峰值及首次达到该值的帧。
3. **支持固定参照批次模式**：CLI 提供 `--no-evolve`（关闭局间改写，全程锁定 `v1`）与 `--seed-version <dir>`（从指定版本目录起跑），方便周二做固定父版 vs. 候选版复测。

---

## 8. 给 Astra 的分阶段施工任务单（Milestone 拆解）

建议 Astra 按以下 4 个阶段拆给下游编码模型，**每完成一个 Milestone 必须跑通对应离线/实机验证后再进下一阶段**：

### M1：搭建 `app/rsi/` 底座核心 + 种子 `v1` + 安全门禁（纯离线可验收）
- **产出文件**：
  - `app/rsi/__init__.py`
  - `app/rsi/primitives.py`（v17 11 键定义、合法性检查、fallback 摘要）
  - `app/rsi/seed_v1.py`（写入初始 `v1/` 的 `strategy.md`, `observer.py`, `tools.py`, `rules.py`, `manifest.json`）
  - `app/rsi/sandbox.py`（AST 静态安全扫描 + 受限 builtins 加载器）
  - `app/rsi/validator.py`（4 道门禁 + 离线轨迹回放 + 父/子版触发差异统计）
  - `validation/test_rsi_validator.py`（单元测试）
- **验收标准**：
  1. 单测覆盖：正常修改通过、越权 import（`os`/`subprocess`）被拒、访问 `__builtins__` 被拒、修改超过 2 个文件被拒、运行时除零异常在回放门禁中被拦截；
  2. 用已有实机存档 `matches/normal-codex/20261123-142700-941090/match-1/observations.json` 与 `match-2/observations.json` 跑通 `v1` 回放测试，确认 `v1` 唤醒次数明显低于原 `match-2` 的 176 次。

### M2：实现 `EngineAdapter` + 局内快慢回路 Runner（`app/rsi/runner.py`）
- **产出文件**：
  - `app/rsi/adapter.py`（`EngineAdapter` 抽象 + `NativeV17Adapter`，复用 `matches.py` / `stage1.py`）
  - `app/rsi/model_client.py`（统一封装 `codex exec` 与 OpenAI HTTP 两种后端，统一提取 `usage` tokens 和耗时）
  - `app/rsi/runner.py`（实现第 5 节完整单帧快慢回路、僵局截断、反射冷却、工具步进保护、运行期异常降级）
  - `validation/test_rsi_runner.py`（用 mock adapter + 真实 `observations.json` 跑通完整局内状态机单测，验证 `rule` / `tool` / `llm` 三种来源、冷却防抖、僵局截断、异常降级）
- **验收标准**：`test_rsi_runner.py` 全绿；确认 `match-2` 这种 `workers=0, army=0` 场景会在设定帧数后自动触发 `cutoff` 而非空转。

### M3：实现局间进化闭环 + CLI 入口（`app/rsi/evolution.py` + `app/rsi_live.py`）
- **产出文件**：
  - `app/rsi/evolution.py`（`review_round(matches: list, ...)` 入口、facts 构造、进化 Prompt、调用 `validator.py`、原子升版/回滚、`evolution-log.jsonl` 追加）
  - `app/rsi_live.py`（完整命令行入口，支持 `--rounds N`、`--matches-per-round 1`、`--speed 42`、`--max-frames 36000`、`--match-timeout 1800`、`--stalemate-zero-frames`、`--live-model`、`--review-model`、`--backend codex|openai`、`--endpoint`、`--no-evolve`、`--seed-version`、`--offline-evolve-test <obs.json>`）
  - `validation/test_rsi_evolution.py`（离线模拟 2 轮进化，验证单局与多局列表输入、`modify` 升版 `v1->v2`、`revert` 回退 `v2->v1`、非法代码被拒保留原版）
- **验收标准**：
  1. 所有单测全绿；
  2. 跑一次真实模型（`--offline-evolve-test`）基于已存档的 `match-2/observations.json` 生成一次真实候选改动，验证它能通过门禁并输出 `candidate-check.json` 与 `evolution-log.jsonl`。

### M4：原速实机联调（2 局）→ 正式 5 局批次
- **执行命令示例**：
  ```powershell
  python app/rsi_live.py --rounds 2 --matches-per-round 1 --speed 42 --max-frames 36000 --match-timeout 1800 --stalemate-zero-frames 1800
  ```
- **验收标准**：
  1. 一次启动连续完成 2 局原速对局 + 局间复盘，全程无人转发或人工点击；
  2. `batch.json`、`timeline.json`、`review.json`、`evolution-log.jsonl` 数据齐全，含原生回执、动作来源计数与 token 统计；
  3. 确认单局无效 `keep_current` 较 `stage2_live.py` 显著下降，且若产生 `v2`，第 2 局实际加载并执行了 `v2`。
  4. 联调通过后，改为 `--rounds 5` 启动正式自主迭代批次。

---

## 9. 未来演进预留（本阶段不写，但保持接口兼容）

- **切 Windows OpenBW CLI + 并行多局（目标：20min 并跑 2–3 局 + 20min 迭代）**：只需在 `app/rsi/adapter.py` 新增 `OpenBWCliAdapter`（复用 `starcraft-cli-lab-20261116` 的 Windows 无头 OpenBW 与引擎停帧能力），并在 `rsi_live.py` 启用多进程并发跑 `--matches-per-round 2..3`，`evolution.py` 和 L1–L3 无需重写。
- **切标准 LLM API**：在 `model_client.py` 已统一抽象 `codex` 与 `openai` 兼容后端，拿到 API Key 后直接切换 CLI 参数即可，不影响版本工作区和日志格式。
- **扩展至 L3b / L4**：未来在 `primitives.py` 增补带坐标原语（如 `attack_point(x, y)`）及 `micro_config.json` 白名单文件，即可平滑把可写边界扩到更多原生按钮、参数化工具和底层微操。
