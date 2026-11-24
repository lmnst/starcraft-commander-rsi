# RSI 修复清单（2026-11-24）

来源：Gemini 提出的两轮问题，逐条对照源码与对局存档（194749 / 211014 / 180332）核实后整理。状态：A、B、C 已实施并离线验收（见文末“实施记录”）；实机 2×2 和 D 阶段未开始。

## 0. 基线与约束

- 测试命令（必须用 3.12；默认 `python` 是 3.9，会因 `write_text(newline=)` 报 39 个错）：
  `py -3.12 -m unittest validation.test_rsi_runner validation.test_rsi_validator validation.test_rsi_evolution`
  当前 43 个全过。每个阶段结束重跑一次，改动到的行为同步改测试。
- 不动 `baseline/`；验证只用日志、JSON 和回放帧号，不截图不录屏（AGENTS.md）。
- 不加向后兼容分支：旧批次存档不需要能被新代码继续跑。

## 默认决策（不同意的直接划掉）

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 局内态势信息由 L0 计算成 `situation` 块，不写进种子 `observer.py` | 不会被进化删掉；observer 保持小，复盘整文件替换不变脆；`enemy_main` 已是 L0 注入的先例 |
| D2 | 防死锁从 L0 prompt 移到种子 `rules.py` 的反射规则 | 194749 三局证明"只讲机制"不够；反射规则确定、零延迟、可被进化调参 |
| D3 | "输了必须改"改成按累计战绩：当前版本 ≥2 负且 0 胜才拒绝 keep | 单局噪声大；但这个复盘模型 keep 倾向强（194749 三轮全 keep），不能纯软引导 |
| D4 | 超时不再判负，判 `inconclusive`；只有近乎拆掉敌主基地才判 `cutoff_win` | 现在拆掉敌主 Nexus 反而判负；超时局不计入 D3 的负场 |
| D5 | Agentic 复盘放进 Docker，只挂载工作区 | Codex sandbox 只限制写不限制读；本机已有 Docker 29.8 和 WSL2 |
| D6 | 错误分类先看消息内容，再看状态码 | 欠费和订阅额度耗尽也是 429 返回 |

## 阶段 A：局内信息 + 防死锁（最高优先级）

### A1 L0 `situation` 块

- 新建 `app/rsi/situation.py`：`build_situation(raw, memory, enemy_main) -> dict`，纯函数，跨帧状态放 `memory`。
- 字段：
  - `own`：战斗单位构成、农民、基地数与坐标、生产建筑、科技建筑、反隐（Observer、Photon Cannon 数量）
  - `army`：`center`，区段 `at_home / mid_map / near_enemy`，到最近己方基地和到 `enemy_main` 的距离，正在交火的己方单位数（`attacking` 或 `cooldown > 0`）
  - `economy`：累计矿气、最近 1200 帧矿气增量、`completed_workers`
  - `enemy`：当前可见的战斗单位和建筑分开计数；侦察记忆，包括出现过的全部敌方类型及首次/最后目击帧、敌方建筑最后已知坐标（上限 20 条）、最后一次看到的敌军规模/构成/位置/帧
  - `defense`：`stance`、`defense_locked_frames`、`frames_since_enemy_near_base`
  - `tasks`：完整语义（`task_id / scope / action / phase / goal / arrived`）
- 接入：`runner._summarize`（[runner.py:369](../app/rsi/runner.py)）在 observer 输出之后，由 L0 写入保留键 `summary["situation"]`；observer 若自己返回这个键则报错。合并后的 summary 仍受 8192 字节限制。
- validator 的离线回放（`_replay_request`）必须做同样的合并，否则候选版本回放和实机看到的输入不一致。
- 复盘的 `state` facts 也带上 `situation` 的精简版，让复盘模型和局内模型看到同一份事实。
- 不做：当前余钱、人口（原生层没输出，不为此重编 DLL）。

### A2 payload 降噪

- `latest_receipt` 中的 `units / army_ids / reserve_units / released_units` 改为只给数量。
- [model_client.py:174](../app/rsi/model_client.py) 改为 `ensure_ascii=False`（模型自己写的中文 reason 现在被转义成 `\uXXXX`，5 条 `recent_decisions` 就占 2.2KB）。

### A3 防死锁改到种子规则

- 删除 [runner.py:40-41](../app/rsi/runner.py) 的硬命令（"defend 且基地无敌人就必须按 `-`，不许 keep"），保留机制说明并补上计时字段的含义。
- [runner.py:38](../app/rsi/runner.py) 中写死 Destination 的那句改成通用表述（与 B2 一起做）。
- `seed_v1.py` 的 `RULES` 加 `release_idle_defense`：`stance == "defend"` 且 `frames_since_enemy_near_base >= 1440` 时按 `-`，reason 写明计时值。阈值作为模块常量，复盘可调。

### A 阶段验收

1. 单测：`build_situation` 的构成、区段、计时、侦察记忆；`release_idle_defense` 的触发与不触发。
2. 离线信息检查：在 194749/match-1（隐刀局）、194749/match-3（金甲局）、211014/match-1（胜局）各取 4–5 个关键帧，导出完整 LLM payload。检查：全部小于 8192 字节；只看 JSON 能否答出"几矿、什么兵、有无反隐、主力在哪、对面什么科技"。
3. 规则影子回放：用 `replay_version` 跑 194749 三局，记录 `release_idle_defense` 本会在哪些帧触发（原局都是按 0 后一直锁到结束）。
4. 决策回放：从 194749 三局"按 0 之后连续 keep"的区间挑约 10 帧，用新 payload 和新 prompt 离线调用局内模型，和原决策对比。结果存到 `matches/rsi/<日期>-decision-replay/`。

## 阶段 B：判定与进化门槛

### B1 超时判定改为 inconclusive

- `adjudication.timeout_judgment`：满足"近乎拆掉敌主基地"仍判 `cutoff_win`，其余全部判 `inconclusive`，保留原因字段。拆掉主 Nexus 后超时从此不再判负（原来走 [adjudication.py:77](../app/rsi/adjudication.py)）。
- 全员阵亡的两条僵局规则仍判 `cutoff_loss`。
- 同步修改：runner 的 `adjudicated_outcome`、`evolution._stats`（新增 inconclusive 计数）、`REVIEW_PROMPT` 中的判定说明（[evolution.py:45-47](../app/rsi/evolution.py)）。

### B2 地图与对手

- 删除 [adjudication.py:22](../app/rsi/adjudication.py) 对 PylonPuller 的限制：花名册 10 个对手全是神族，按 Nexus 判定的规则对它们都适用。改为按花名册的 `race` 检查，非神族时 site 为 None 并写进 cutoff 记录。
- `DESTINATION_STARTS` 改成 `MAP_STARTS`，覆盖 `matches.MAPS` 中的 3 张图（都是 2 人图）。Benzene 和 Heartbreak Ridge 的坐标优先从 scx 的 CHK 提取（`validation/destination-base.chk` 说明以前做过类似提取），提取不了就各跑一局，读开局己方 Nexus 的位置。
- `rsi_live.py` 启动时，地图不在 `MAP_STARTS` 就直接报错退出，不允许静默降级。现状是：换图后整局 `enemy_main` 都是 None，即使看到敌方主基地也不会更新，6 键等于废掉，超时也全部判负。

### B3 进化门槛改为累计口径

- 修改 [evolution.py:499-505](../app/rsi/evolution.py)：只有当前版本累计（`version_history` 加上本轮，注意本轮还没写进 `batch.json`）负场（`loss + cutoff_loss`）≥ 2 且胜场（`win + cutoff_win`）= 0 时，才拒绝 keep。`inconclusive`、`error`、`degraded`（见 C3）都不计。
- `REVIEW_PROMPT` 第 66–70 行改成相同口径，并鼓励"新版本比父版本明显退步时 revert"。
- 修改测试 `test_seed_history_is_visible_and_keep_on_loss_is_retried`。

## 阶段 C：容错、计费与模型调用

### C1 错误三分类（`model_client.py`）

- `fatal`：先匹配消息内容，包括 usage limit、`insufficient_quota`、billing、credits、无效或过期的 key、未登录；再看状态码 401、402。
- `transient`：没命中 fatal 的 429，以及 5xx、超时、连接中断。
- 熔断器只统计连续的 `fatal`（3 次）。修改 [model_client.py:19-31](../app/rsi/model_client.py) 的正则和 [:62](../app/rsi/model_client.py) 的 `_provider_failure`（现在先看状态码，必须改成先看消息）。
- Codex 额度耗尽的原文（180332 实测）："You've hit your usage limit ... try again at 7:42 PM"。解析出 `retry_at` 写进熔断记录；"暂停到这个时间再继续"作为可选项，本轮不做。

### C2 复盘退避

- `_run_review_attempts`（[evolution.py:543](../app/rsi/evolution.py)）：遇到 `transient` 按 5s / 15s / 45s 退避，不消耗 3 次格式重试的名额。transient 累计等待超过 10 分钟，本轮记为 `model_unavailable` 并停批（单独的退出码）。
- 现状：复盘的重试是零间隔连发 3 次，连续 3 次 429 会直接打开熔断。

### C3 局内调用

- 不在外层重试：Codex CLI 自己会重试（180332 的 stderr 里有 `retrying sampling request (5/5 ...)`），而且超过 24s 的决策本来就作废。
- `transient` 不计入熔断。一局中局内调用失败率超过 50% 的，标记为 `degraded`：不计胜负，不参与 B3 的门槛；复盘仍能看到它，但会注明。

### C4 单局崩溃

- `_play_one` 改为捕获 `Exception`（[rsi_live.py:245](../app/rsi_live.py) 现在只捕获 4 类，KeyError、TypeError 会直接让整批崩掉）。
- 删除 [rsi_live.py:289](../app/rsi_live.py) "本轮有 error 就退出整批"。改为连续 3 局 error 才停批。

### C5 token 统计不再被一次缺失清空

- `runner._settle_reply`（[runner.py:513](../app/rsi/runner.py)）：已知 token 照常累加，缺失次数单独记，做法照搬复盘侧现有的 `known_usage + usage_missing_counts`（[evolution.py:347-367](../app/rsi/evolution.py)）。
- `_sum_field`（[evolution.py:323](../app/rsi/evolution.py)）汇总已知值，并同时给出缺失次数。已知值只是下限：超时的调用同样会计费。
- 180332 实际情况：match-2 不是"1 次超时"，而是额度耗尽导致连续 29 次失败（decision-050~078）。

### C6 复盘的推理强度参数

- 新增 `--review-reasoning-effort`（默认 medium，HTTP 后端可设为 `omit`），替换 [rsi_live.py:317](../app/rsi_live.py) 和 [:321](../app/rsi_live.py) 的写死值。

### C7 剥 markdown 围栏

- `_decode`（[model_client.py:122](../app/rsi/model_client.py)）只剥最外层一对 ` ```json ` 或 ` ``` `，不做"从文本中提取第一个 `{...}`"。参照 [stage2_live.py:84](../app/stage2_live.py)。

### C8 启动前预检

- 开第一局前，局内和复盘两个客户端各用真实的请求结构发一次极小的调用（要求返回 `{"ok": true}`）。任何失败都在开局前退出，结果写入 `preflight.json`。可以提前发现参数不被接受、密钥失效、返回格式不对等问题。

### C 阶段验收

- 单测：错误分类表（用 180332 的真实错误文本，加上 429 + `insufficient_quota`、纯 429、503、401 各一例）、退避计时（注入 sleep）、degraded 判定、token 累加、围栏剥离、预检失败退出。
- 离线：`--offline-evolve-test` 跑一遍 211014/match-1，确认复盘流程未退化。

## 阶段 D：Agentic 查案式复盘

### D1 工作区

- 新建 `app/rsi/agentic_review.py`。每轮在批次目录外建 `review-workspace/`：
  - `evidence/`（只读）：`match.json`、`observations.json`、`timeline.json`、己方 `native-receipts`、`version_history.json`、每帧 `situation`
  - `candidate/`（可写）：当前版本 4 个白名单文件、`CONTRACTS.md`、`check.py`
  - `handoff.json`：结案报告
- 绝不拷入：`opponent/`（里面有对手视角的 `commander-layer.jsonl`）、`app/`、`baseline/`。

### D2 隔离（未采用 Docker，改为 L0 工具循环，见文末实施记录）

- 在 Docker 中运行 Codex，只挂载 `review-workspace`（evidence 只读）。Codex 登录凭据以只读方式挂载；agent 理论上能读到这份凭据，记为已知风险。
- 对局结束后，把 `match-N/opponent/bwapi-data/write/` 移到批次目录下不挂载的私有目录。
- L0 审计 `review-trace.jsonl`：出现工作区以外的路径，本轮作废。

### D3 交接与终审

- `handoff.json`：`decision / diagnosis / hypothesis / expectation / evidence / distillation_type`。evidence 改为"帧号 + 文件"引用，[validator.py:288-293](../app/rsi/validator.py) 的 fact_id 校验也要相应改。
- L0 只读取 4 个白名单文件和 `handoff.json`，独立运行 `validate_candidate`。B3 门槛同样适用。
- 超时上限 10 分钟，单独记 token。agentic 复盘失败按 C2 处理，不回退到旧的单轮复盘。

### D 阶段验收

- 用 194749 的存档在临时批次里离线跑一次完整流程：查案、改文件、自测、L0 终审升 v2。
- 检查 trace 里没有越界读取；人工读一遍 diagnosis 是否引用了真实帧号。

## 顺序与实机验证

1. A → B → C 在同一个会话里按顺序做（都会改到 `runner.py` 和 `evolution.py`）。
2. A+B+C 完成后跑一次实机批次：2 轮 × 2 局，开启进化。检查 `situation` 进了 payload、防死锁规则的触发记录、token 统计、batch.json 中的 inconclusive 计数。
3. D 先离线跑通，再上实机。

## 明确不做

- 超时加时机制：L0 拿到的信息不足以判断该不该加时。
- 剩余矿气、人口字段（需要重编原生 DLL）。
- 局内调用的外层重试。

## 实施记录（2026-11-24，A/B/C）

- 测试：`py -3.12 -m unittest validation.test_rsi_runner validation.test_rsi_validator validation.test_rsi_evolution validation.test_rsi_situation validation.test_rsi_resilience`，64 个全过（原 43 个，新增 21 个；改动行为的旧用例已同步修改）。
- 离线验收产物：`matches/rsi/20261124-offline-check/`
  - `payloads/index.md`：3 局历史对局、共 18 帧重建后的局内 payload。summary 最大 2622 字节（上限 8192），单次局内请求约 6.8k 字符（原约 6.3k）。
  - `shadow.json`：种子防死锁规则的影子回放。194749/match-3 原局在 12603 帧按 0 后一直锁到输，规则会在 14160 帧解除；194749/match-1（隐刀局）锁防期间基地一直有敌人，规则正确地没有触发。
  - `decision-replay/`：从 194749 三局"锁防且基地附近一段时间没有敌人"的时刻挑了 7 帧，payload 已生成，**还没调用模型**。拿到 API 后运行 `py -3.12 validation/rsi_offline_check.py decision-replay --call --backend openai --endpoint <URL> --model <模型>`。
- 复盘输入变大：每局的 facts 约从 23k 增加到 47k 字符（因为 state 事实里加了 situation）。

与清单不同的地方：
- A1：`situation` 同时写进 `observations.json` 的每一行，observer 也能在 raw 里读到。回放时 L0 总是在完整数据流上重新计算，旧存档也能直接用。
- B2：没有做按种族的检查。超时已改判 inconclusive，非神族对手本来就不可能拿到 `cutoff_win`，所以直接删掉了对 PylonPuller 的限制。Benzene、Heartbreak Ridge 的出生点由 `validation/map_start_locations.py` 从地图文件中提取；同一脚本提取的 Destination 坐标与原来写死的值一致。
- B3：没有父版本时选 revert，现在作为格式错误打回重试（原来是 gate4 拒绝、不重试）。
- C2：额度耗尽、密钥失效这类致命错误在复盘中仍保留原有的重试（最多 3 次），熔断依靠这几次重试才能打开。
- C1：额度耗尽时把 "try again at …" 写入熔断原因；"暂停到恢复时间再继续"没有做。

## 实施记录（2026-11-24，D 与实机）

D 没有按 D2 用 Docker，改成 L0 实现的工具循环（`app/rsi/agentic_review.py`，`--review-mode agentic`，只支持 HTTP 后端）：

- 模型拿不到 shell 和文件路径，只能调用 9 个工具：`list_evidence`、`get_frames`、`get_timeline`、`run_analysis`、`read_policy`、`write_policy`、`discard_policy`、`check_candidate`、`submit`。
- 工具只读本轮己方存档（observations 含 situation、timeline、native receipts、runtime errors、L0 结果）和当前版本源码，`opponent/` 不读。D2 担心的"agent 能读到凭据"因此不存在。
- `run_analysis` 在独立子进程里执行，AST 限制与策略代码相同，超时 20 秒。
- `submit` 经过 `_decision_from_report`（含 B3 门槛）和 `check_candidate`，最多被打回 3 次，之后照常走 gate1–4。
- 上限：20 轮工具调用、900 秒。每轮写 `review-trace-N.jsonl` 和 `review-workspace-N/`。
- 离线验收：`matches/rsi/20261124-agentic-offline/`，5 轮、58 秒，v1 → v2 通过。

实机中发现并修复的问题：

- 游戏目录：新环境里没有 `STARCRAFT_GAME` 和 `local.settings.json`，于是退回到不存在的 `E:\StarCraftAI`。现已补上 `local.settings.json`，`rsi_live` 启动时先检查游戏目录（失败批次：`20261124-121211-457332`）。
- 局内决策的 reason 超长：Opus 写的理由有 133–407 字，超过 `MAX_REASON_CHARS = 160` 后整条决策作废。`20261124-132204-490957/match-1` 中 16 次决策有 15 次因此作废，其中 10 次是想按 6 进攻。
  - 修复：prompt 写明 reason 上限 160 字、`args` 不用时传 `{}`。runner 遇到超长 reason 时截断并标记 `reason_truncated`，缺 `args` 按 `{}` 处理，不再作废合法的命令。
  - 该批次已手动停止，未做复盘。
- 跨批次预测：从别的批次的 `versions/vN` 播种时，`previous_prediction` 现在会取种子批次里产出 vN 的那次复盘。

并行（`--parallel N`，启动脚本对应 `-Parallel`）：

- 同一轮的对局分波同时开打，结果按局号排序。
- 原来每局都要拿全局锁，现在由 `rsi_live` 在整个批次期间持有一次，各局用 `hold_lock=False`。
- 无需修改的部分：
  - SNP 按 `COMMANDER_NETWORK_SCOPE`（每局随机 room）隔离 mutex 和共享内存。
  - 按键用 PostMessage 发到本局窗口，由 `WH_GETMESSAGE` 钩子接收，不依赖焦点。
  - 窗口管理器只管理本局路径下的进程。

实机后续修复（`20261124-133626-533308` 之后）：

- 回复格式：Opus 有时先写一段分析，再给代码块或裸 JSON（match-1 decision-006、match-2 decision-010）。`_decode` 改为：回复本身不是 JSON 时，文本里恰好有一个 JSON 对象才采用。prompt 也写明只回 JSON。用新规则重新解析两批的所有真实回复：原来失败的 3 条全部恢复，其余结果不变。
- 复盘成本：一次 agentic 复盘 9 轮、586k input tokens，因为每轮都重发约 50k 的 facts。现在 system 和每次请求的最新一条消息都打上 `cache_control` 断点。实测 router 会把它透传给 Anthropic，第 2、3 轮分别有 80.6%、83.8% 的 input 命中缓存，tool 消息也能缓存。
- 批次正常结束时写入 `state: completed`。
