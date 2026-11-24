# 星际指挥台自主迭代（RSI）设计与实验指南

> **面向读者**：刚接触本项目的同事或协作者。读完这篇文档，你可以搞清楚我们在做什么、系统架构怎么分层、一轮实验是怎么自动跑起来的、怎么动手复现，以及目前已经跑出了什么真实结果。  
> **适用代码分支**：`rsi-live-loop`（基于 `v17` 原生指挥台）  
> **更新日期**：2026-11-24

---

## 一、我们在做什么？

一句话概括：**把“人按 11 个高层按钮打《星际争霸：母巢之战》”，升级成“大模型一边实时指挥对战，一边在局间自己查日志复盘、写 Python 代码、造工具、沉淀反射规则的自主进化闭环（RSI）”。**

和传统强化学习（动辄需要上亿局模拟）不同，我们想验证的是**低样本、代码级进化**的路线：
- 每轮只打少量真实原速对战（目前默认每轮 2 局并行）；
- 局间让推理模型像数据分析师一样查案复盘，定位最核心的问题；
- 不只是改几句自然语言 Prompt，而是直接把反复出现或对延迟敏感的判断**蒸馏成可复用的 Python 观测器、复合工具和 0 延迟自动反射规则**；
- 最终希望在几十到几百局内，看到清晰的自主演进轨迹：版本持续迭代、无效模型调用下降、实战表现和跨对手泛化能力提升。

---

## 二、系统架构：锁死的底座（L0）+ 可进化的三层（L1–L3）

为了既让模型能改代码、又绝对防止它作弊（偷看对手视野）或把程序改崩，整个系统按权限严格分成 **不可变底座（L0）** 和 **可进化工作区（L1–L3）**：

```text
┌──────────────────────── L0 不可变底座（人写好，永远锁死） ────────────────────────┐
│  1. 原生星际引擎 + 冻结底层 Bot (baseline/Baseline.dll) + v17 指挥层 (11个按钮)    │
│  2. 合法观测采集（只读己方视野）+ 客观态势摘要 situation（L0 自动计算，不可篡改）  │
│  3. 原子按键派发与原生回执核对 │ 僵局/超时裁判 │ AST 安全沙箱 + 4道升版门禁 │ 日志  │
└────────────▲─────────────────────────────────┬──────────────────────────▲─────────┘
             │ (4) 发原生按键并核对回执         │ (1) 吐出合法观测流       │ (5) 全过门禁才升版
┌────────────┴─────────────────────────────────▼──────────────────────────┴─────────┐
│                   Agent 可进化工作区（versions/vN/，局间模型可改）                  │
│                                                                                   │
│  [L1] strategy.md  —— 战略原则、对手特点与战术备忘（供实时 LLM 阅读，≤ 2KB）       │
│  [L2] observer.py  —— 战报加工 summarize() + 哨兵 should_wake_llm()（控频降费）    │
│  [L3a] tools.py    —— 基于 11 键的步进式状态机复合工具（多步组合动作）             │
│  [L3b] rules.py    —— 自动反射规则 decide_reflex()（0 延迟、0 Token 肌肉记忆区）   │
└────────────▲────────────────────────────────────────────────────────────▲─────────┘
             │ 局内快慢双回路：L2 摘要 → L3a 步进 → L3b 反射 → 按需唤醒实时 LLM
             └─────────────── 局间慢回路：Agentic 查案复盘 → 改代码 → 过门禁升版 ────┘
```

### 1. 为什么底座（`Baseline.dll`）要先冻结？

很多同学第一眼会问：“底层 `Baseline.dll` 只有约 600 行 C++，运营很朴素，为什么不先改强它？”

原因有两点：
1. **控制变量（做干净的对照实验）**：固定同一个底座，才能客观衡量“RSI 自主进化本身”带来了多少提升，而不把“人改底座”和“模型自进化”混在一起。
2. **先跑稳纯 Python 进化闭环，再按需放开底层（L4）**：纯 Python 层的修改可以毫秒级做 AST 安全扫描和离线轨迹回放校验；等 L1–L3 跑稳并撞到明确的底层天花板（例如反隐建筑时机、开矿/科技排程）后，再开放下一阶段的 **L4（宏观建造/科技原语或运营配置白名单）**。

### 2. 模型手里能按哪些按钮？（v17 的 11 个原子原语）

底层 `Baseline.dll` 负责常规采矿、造建筑、升科技和产兵；上层指挥官（规则 / 工具 / 实时 LLM）通过以下 11 个高层键接管部队与采矿：

| 按键 | 含义 | 行为说明 |
| --- | --- | --- |
| `1` | 探机避险 | 救回正在侦察或受威胁的探机 |
| `2` | 强制采矿锁 | 探机专心采矿不追敌，保留采气与建造 |
| `3` | 抽调动态小队 | 需 ≥ 8 个健康兵，抽约 1/4 成小队；**主力锁定守家** |
| `4` | 切换全军范围 | 仅改变后续命令的作用范围，**不解除已有锁防或任务** |
| `5` | 进攻近敌/分矿 | 沿途先打敌军和炮台，目标为近处敌建筑或已知分矿 |
| `6` | 敌主矿清场 | 进攻已知敌方主基地（2 人图开局可从地图出生点推算） |
| `7` | 就近警戒 | 持续就近防守，忽略探机引诱 |
| `8` | 前线集结 | 原地集结等待援兵 |
| `9` | 安全撤离 | 先脱险，再到共同安全点集结 |
| `0` | 全军回防 | 召回全军并**锁定守家**，清除小队模式，保留采矿锁 |
| `-` | 交回基线 | 解除手动任务与守家锁，交回 `Baseline.dll` 自动控制 |

> **关键边界**：目前没有建造和科技按钮，所以面对速隐刀（Dark Templar）这类战术，L1–L3 只能在**战术上缓解**（例如抢在隐刀出来前强攻、撤退保兵等），无法直接命令家里提前造反隐建筑（这属于后续 L4 扩展范围）。

### 3. 发给模型的战场信息（L0 `situation` 摘要）

每 120 帧（标准速度下约 5 秒），L0 从原生日志读取我方合法观测，并自动计算出一份只读的 `summary["situation"]` 注入给规则、工具、实时 LLM 和复盘模型，包含：
- **己方家底（`own`）**：农民数、各兵种数量、基地数与坐标、生产建筑（BG/VR/VS）、科技建筑、反隐单位（Observer / Photon Cannon）、在建单位；
- **部队态势（`army`）**：主力中心坐标、所在区段（`at_home` / `mid_map` / `near_enemy`）、距最近己方基地和敌方建筑的距离、双方当前正在交火的单位数（`engaged` / `enemy_engaged`）；
- **经济（`economy`）**：累计矿气、最近 1200 帧矿气增量、完工农民数；
- **敌方情报与侦察记忆（`enemy`）**：当前可见敌军/建筑/农民，以及**脱视野后依然记住的**敌方已目击兵种首末帧、已知敌建筑坐标、最后一次与峰值敌军规模；
- **防守计时（`defense`）**：当前姿态、`defense_locked_frames`（锁防持续帧数）、`frames_since_enemy_near_base`（基地多久没见敌人）——用来彻底避免“按 0 锁防后忘了按 `-` 导致全军在家发呆到输”的死锁；
- **当前任务（`tasks`）**：活跃任务的 `action / phase / scope / goal / arrived`。

---

## 三、一轮实验是怎么自动跑起来的？

### 阶段 1：局内快慢双回路（每局约 8～12 分钟，支持 2 局并行）

每当 L0 收到一帧新的观测（每 120 帧一次），按以下优先级顺序处理：

1. **僵局与超时检测（L0）**：
   - 默认标准速度 `42 ms/帧`（24 fps），单局上限 `36000 帧`（约 25 分钟）或墙钟 `1800 秒`；
   - 如果己方连续 `360 帧` 零农民零兵，或连续 `480 帧` 零农民且部队 ≤ 1，判定为无力回天，立即原生离局并记为 `cutoff_loss`（避免垃圾时间烧钱）；
   - 若打到时间/帧数上限：只有法律视野证据确认“敌方主基地濒毁且正被持续围攻”才判 `cutoff_win`，其余全部记为 `inconclusive`（不冤判负）。
2. **生成摘要（L2）**：调用 `observer.summarize()`，L0 自动合入不可篡改的 `situation`；若代码抛异常则记入 `runtime-errors.json` 并无缝降级，保证对局不崩。
3. **处理在途 LLM 回复**：如果上一次异步模型请求已返回且未超 24 秒时效预算，校验合法后执行对应 `key` / `tool` 或 `keep_current`。
4. **步进复合工具（L3a）**：如果当前有运行中的 `active_tool`（如“先按 `3` 切小队 → 等确认 → 再按 `5` 进攻”），推进异步状态机一步（受最大步数/帧数保护）。
5. **自动反射规则（L3b）**：在冷却期外调用 `rules.decide_reflex()`。命中时 **0 毫秒延迟、0 Token** 直接发键或启动工具（内置种子规则 `release_idle_defense` 会自动解除空转过久的守家锁）。
6. **哨兵按需唤醒实时 LLM（L2 → System 2）**：若当前无在途请求、未在等回执且刚未触发反射，调用 `observer.should_wake_llm()`；仅在开局、接敌变化、兵力突变、基地/任务事件或长周期兜底（1800 帧）时异步调用实时模型（推理强度 `low`），大幅压缩无效 `keep_current` 轮询。

### 阶段 2：局间 Agentic 查案复盘（每轮约 1～3 分钟）

一轮所有对局（如 2 局并行）全部结束后，L0 自动启动 **Agentic 复盘**（`app/rsi/agentic_review.py`），让高推理强度模型（`high`）像工程师查案一样自主分析：

- **强隔离工具箱（无 shell、无宿主机路径、严禁接触对手目录）**：
  - 查证据：`list_evidence`、`get_frames`（按帧区间抽查全量观测与 `situation`）、`get_timeline`（查每条指令与回执）、`run_analysis`（在独立 AST 受限子进程里写 Python 跑全帧统计）；
  - 改方案：`read_policy`、`write_policy`（单轮最多改 2 个白名单文件）、`discard_policy`；
  - 自测与交卷：`check_candidate`（提前跑 L0 安全与影子回放自检）、`submit`（提交诊断、假设、下一轮可量化预测与决定 `keep / modify / revert`）。
- **防乱改与防躺平机制**：
  - 启用了 Prompt Caching，多轮工具调用共享前缀，实测缓存命中率达 **79%–88%**；
  - 累计战绩门槛：允许在单局噪声下选择 `keep`，但一旦当前版本累计 **≥ 2 负且 0 胜**（不含 `inconclusive` 和 `degraded`），L0 会拒绝 `keep`，要求提交一个小而具体的 `modify` 实验或 `revert` 回退父版本。

### 阶段 3：4 道 L0 硬门禁（过关才升版）

模型提交 `modify` 后，L0（`app/rsi/validator.py`）独立执行 4 道检查，任一道不通过即拒收并保持原版继续下一轮：

1. **门禁 1（白名单与体量）**：只能修改 `strategy.md`（≤ 2KB）及 `observer.py` / `tools.py` / `rules.py`（各 ≤ 12KB）中的 1～2 个文件，且必须引用真实存在的事实 ID。
2. **门禁 2（AST 静态安全扫描）**：只允许导入 8 个纯计算标准库（`math`, `collections`, `statistics`, `itertools`, `functools`, `copy`, `typing`, `dataclasses`）；彻底禁止 `os`/`subprocess`/`open`/`eval`/`exec` 及所有双下划线反射属性。
3. **门禁 3（离线影子回放 Shadow Replay）**：在独立沙箱子进程（≤ 3 秒超时）里，拿本轮真实对局的完整观测流把候选代码逐帧回放一遍，检查无异常、无超限、所有工具可冒烟通过，并输出“父版 vs 候选版”的唤醒帧/反射帧客观差异报告（`candidate-check.json`）。
4. **门禁 4（原子升版与留痕）**：生成自包含快照 `versions/v{N+1}/`，原子切换 `active.json`，追加一条结构化记录到 `evolution-log.jsonl`。

---

## 四、怎么动手跑实验？

### 1. 环境准备

- **系统与游戏**：Windows 10/11，已安装 **StarCraft: Brood War 1.16.1**（不支持重制版），在仓库根目录的 `local.settings.json`（不进 git）配置 `"game": "<你的1.16.1目录>"`（或设环境变量 `STARCRAFT_GAME`）。
- **Python**：使用 **Python 3.12**（`py -3.12`）。
- **API 配置**：设置环境变量 `OPENAI_BASE_URL`（或 `RSI_API_ENDPOINT`）和 `OPENAI_API_KEY`（也可通过启动脚本 `-Endpoint` / `-ApiKeyEnv` 指定，密钥绝不写入任何日志或 git）。
- **内置对手与地图**：仓库已集成 10 个神族 Bot（`PylonPuller`, `Locutus`, `Stardust-SourceRepairCandidate`, `McRave`, `BananaBrain`, `Steamhammer`, `PurpleWave`, `HladHammer`, `tscmoo-AuthorCandidate`, `Skynet`）和 3 张 2 人图（`Destination`, `Benzene`, `Heartbreak Ridge`）。
  - *注意*：已知 `PurpleWave + Heartbreak Ridge`、`Skynet + Destination` 2 组在兼容短测中异常，启动器会自动拦截拒绝运行。
- **测试约束（`AGENTS.md`）**：所有验证只读日志、JSON 与回放帧号，默认禁止截图/录屏（`COMMANDER_ENABLE_SCREEN_CAPTURE=OFF`）。

### 2. 常用命令

**（1）先跑离线自检（0 成本，不启动游戏，约 30 秒）**
```powershell
# 核验冻结底座与 10 个对手哈希
py -3.12 app/matches.py --validate

# 跑全部 72 个 RSI 单元测试
py -3.12 -m unittest validation.test_rsi_runner validation.test_rsi_validator validation.test_rsi_evolution validation.test_rsi_situation validation.test_rsi_resilience validation.test_rsi_agentic
```

**（2）启动多对手轮换 + 2 路并行的实机 RSI 批次**
```powershell
& .\scripts\rsi_run.ps1 `
    -Rounds 10 -MatchesPerRound 2 -Parallel 2 `
    -Map Destination `
    -Opponent PylonPuller,Locutus,Stardust-SourceRepairCandidate,McRave,BananaBrain,Steamhammer,PurpleWave,HladHammer,tscmoo-AuthorCandidate
```

- **从已有版本续跑**：加 `-SeedVersion matches\rsi\<旧批次>\versions\vN`（自动继承上一轮的预测与战绩）。
- **固定版本对照（不进化）**：加 `-NoEvolve`。
- **短局冒烟测试**：加 `-MaxFrames 3600`（每局约 2.5 分钟）。
- **安全停止**：终端按 `Ctrl+C`，程序会原生离局保存回放，并将该局标为 `manual_stop`（不要强杀进程，否则 `batch.json` 不会收尾）。

### 3. 实验产物怎么看？

每次启动都会在 `matches/rsi/<时间戳>/`（不进 git）生成完整可复盘档案：

| 文件 / 目录 | 看什么 |
| --- | --- |
| `batch.json` | 整批总览：启动参数、每局胜负/帧数/耗时/动作来源计数、每轮复盘结论、总 Token 消耗与批次终态 |
| `active.json` | 当前生效的策略版本指针 |
| `versions/vN/` | 每个版本的完整 4 个文件（`strategy.md` / `observer.py` / `tools.py` / `rules.py`）和 `manifest.json` |
| `evolution-log.jsonl` | 进化时间线：每轮升版/保留/回滚、修改的文件与函数名、`distillation_type`、Token 统计 |
| `match-N/match.json` | 单局 L0 结算、锁防占比（`lock_defense`）、兵力峰值（`army_peaks`）、`degraded` 健康状态 |
| `match-N/timeline.json` | 逐条记录每个动作的来源（`rule` / `tool` / `llm`）、状态与原生按键回执 |
| `match-N/decision-NNN.json` | 每次局内实时调用的完整输入 JSON、输出回复与耗时/Token |
| `match-N/review.json` & `review-trace-N.jsonl` | 每轮 Agentic 复盘的查案工具调用过程、诊断依据、diff 与门禁结果 |
| `match-N/maps/replays/*.rep` | 原生星际录像，可直接用仓库里的桌面回放工具打开观看 |

---

## 五、目前已跑出的真实结果（2026-11-24）

截至今天下午，我们已经完成了全链路实机验证，并启动了首批**9 对手轮换、2 路并行、Agentic 复盘**正式实验（详见 [RSI_EXPERIMENT_20261124.md](RSI_EXPERIMENT_20261124.md)）：

### 1. 工程指标已验证达标
- **双局并行稳定**：两局原生星际同时以标准速度 24 fps 推进，无帧率拖慢。
- **容错与零崩溃**：全部测试对局 `runtime-errors.json` 为空；修复局内回复解析后 `invalid` 降为 **0**；Agentic 复盘开启 Prompt Caching 后缓存命中率稳定在 **79%–88%**。
- **实机排雷记录**：第 4 轮实机首次完整跑到 Java 对手 `PurpleWave` 时，排查出本机 JDK 21 缺少 `--add-opens=java.base/java.nio=ALL-UNNAMED` 导致对手进程启动即崩（该局记为无效并及时中止避免污染复盘），已在 `app/matches.py` 中修复。
- **开跑前 72 个单测 + 底座校验全绿**。

### 2. 真实自主代码进化轨迹（全部由模型自己查案写出并通过门禁）

| 进化节点 | 复盘发现了什么问题 | 模型自主改了什么代码 | 后续实战验证 |
| --- | --- | --- | --- |
| **初始 → v1**（原 133626 `v2`） | 打 `PylonPuller` 连输 2 局：部队在敌家附近空等 ~800 帧，等到隐刀出来后被砍崩 | 在 `rules.py` 新增反射规则 `early_commit_enemy_main`（≥ 8 健康兵在敌建筑附近且视野无隐形威胁时自动按 `6` 强攻）+ 更新 `strategy.md` | 下一轮 `A-1` 对 `PylonPuller` 时 LLM 结合新策略在第 6840 帧提前按 `6` 拿下**自然胜利**（第 12061 帧）；第 4 轮 `B-5` 验证了规则机制本身按设计在 9360 帧触发（但该局因对手 Java 崩溃不计有效胜场） |
| **v1 → v2**（B 批第 2 轮） | 打 `Stardust` 时，种子规则 `release_idle_defense` 在基地外 384px 激烈交战中（14 龙骑围攻）误按 `-` 解锁防守 | 在 `rules.py` 自主新增 `_enemies_near_army()` 函数修复 Bug：主力附近有敌军或正在交火时禁止解锁 | 影子回放确认修复了 11400 帧误触发；后续对局未再出现战斗中误解锁 |
| **v2 → v3**（B 批第 3 轮） | 打 `BananaBrain`（大量龙骑）和 `Steamhammer`（前置光炮阵）时，部队在中场单方面挨打（`enemy_engaged ≥ 3` 但我方 `engaged = 0`），等 LLM 反应过来兵已掉半 | 在 `rules.py` 新增反射规则 `withdraw_one_sided_fight`（单方面挨打且敌兵力占优时 0 延迟按 `9` 撤退）+ 更新 `strategy.md` 记下两对手特征 | 已通过 AST + 影子回放门禁（确认在 `B-3` 历史轨迹上能比原局更早触发撤退），待下一批多对手续跑实测 |

---

## 六、代码导航与下一步计划

### 核心目录速查
- `app/rsi/`：L0 底座核心
  - [adapter.py](../app/rsi/adapter.py)：引擎抽象与 `NativeV17Adapter`
  - [situation.py](../app/rsi/situation.py)：L0 态势摘要计算
  - [runner.py](../app/rsi/runner.py)：局内快慢双回路主循环
  - [agentic_review.py](../app/rsi/agentic_review.py)：局间 L0 托管工具复盘循环
  - [evolution.py](../app/rsi/evolution.py)：事实采样、战绩口径、版本谱系与批次记账
  - [sandbox.py](../app/rsi/sandbox.py) & [validator.py](../app/rsi/validator.py)：AST 安全扫描与 4 道门禁
  - [adjudication.py](../app/rsi/adjudication.py)：地图出生点与超时裁定
  - [model_client.py](../app/rsi/model_client.py)：HTTP/Codex 客户端、错误三分类与熔断器
  - [seed_v1.py](../app/rsi/seed_v1.py)：默认 `v1` 种子策略
- [app/rsi_live.py](../app/rsi_live.py) & [scripts/rsi_run.ps1](../scripts/rsi_run.ps1)：命令行入口与启动脚本
- `validation/test_rsi_*.py`：72 个离线单元测试
- 其他详细技术文档：[RSI_L2_L3_ARCHITECTURE_SPEC.md](RSI_L2_L3_ARCHITECTURE_SPEC.md)（完整施工规范）、[RSI_HANDOFF_20261124.md](RSI_HANDOFF_20261124.md)（运行交接细节）、[RSI_EXPERIMENT_20261124.md](RSI_EXPERIMENT_20261124.md)（逐轮原始实验记录）

### 下一步路线（按顺序推进）
1. **完成本轮 L1–L3 多对手轮换批次 + 固定版本对照**：沉淀完整的跨局进化曲线和“L1–L3 能力边界”客观证据；
2. **开放轻量 L4（宏观运营/反隐权限）**（已开始，见 [RSI_L4_MACROBOT.md](RSI_L4_MACROBOT.md)）：基于实测瓶颈，先以最小改动开放 2～3 个高层运营口子（如优先出反隐/提前开矿原语，或可进化 `macro_config.json` 阈值表），无需让模型直接改 C++；
3. **大规模并发扩展（远期）**：当需要上无头服务器冲 200+ 局/周时，新增 `OpenBWAdapter` 替换最底层适配器即可，上层 L1–L3 与复盘代码零改动。
