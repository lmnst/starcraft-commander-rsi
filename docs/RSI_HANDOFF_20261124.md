# RSI 正式实验交接（2026-11-24）

本文档交给接手正式实验的会话。之前的修复过程见 [RSI_FIX_PLAN_20261124.md](RSI_FIX_PLAN_20261124.md)，架构见 [RSI_L2_L3_ARCHITECTURE_SPEC.md](RSI_L2_L3_ARCHITECTURE_SPEC.md)。

## 1. 现在的状态

全链路已在实机上跑通一轮：`matches/rsi/20261124-133626-533308`，1 轮 × 2 局，两局同时进行。

| 环节 | 结果 |
|---|---|
| 预检 | live 和 review 两路都通过 |
| 并行开局 | 两局在 13:37:04 同时进入对局，帧率都是 24 fps（41.7 ms/帧，即标准速度） |
| 局内 LLM → 按键 → 原生回执 | 第 1 局第 8642 帧按 `6`，第 2 局第 8762 帧按 `0`，回执都是 `applied` |
| situation | 进了每次局内请求的 payload，模型的理由里引用了它（例如部队位置、DT 数量、有没有反隐） |
| 防守解锁规则 | 第 2 局锁防期间敌人一直在基地里，规则正确地没有触发。实机触发至今还没观察到 |
| 结果 | 第 1 局自然败（14975 帧）；第 2 局 `cutoff_loss`（11520 帧，工人为 0、部队 ≤ 1）。两局都输给了 PylonPuller 的 DT |
| agentic review | 9 轮工具调用，以 submit 结束，v1 → v2 通过（modify / distill_rule） |
| v2 的内容 | 新增 reflex `early_commit_enemy_main`：在 DT 出现之前，部队已经到了敌方主基地附近、而敌方可见兵力很少时，按一次 `6`。另在 strategy.md 里写明：没有反隐时不要反复按 6 或 5 |
| v2 自检 | 我用 shadow replay 在两局的数据上各验证了一次：都在第 7440 帧触发一次，v1 则不触发 |
| **还没验证** | v2 在实机里的表现。它的第一次实战就是正式实验的第 1 轮 |
| **还没验证** | 这个批次之后的修复（新的 `_decode`、复盘 prompt caching、`state: completed`）只跑过单测和真实回复的重新解析，外加一次 3 轮的缓存探测，还没在完整批次里跑过。正式实验第 1 轮结束后要核对：`decision_sources.invalid`，以及 `review_usage.cached_input_tokens` 是否远大于 0 |

本次 review 的 expectation（写给下一次 review 核对的预测）：

> 下一轮里，只要部队到了 near_enemy，`early_commit_enemy_main` 就应该在第一次看到 DT 之前触发，并且在 DT 接触之前，进攻任务的 arrived 应该 > 0，或者能看到主基地里的敌方建筑。如果依然零胜，经济照样被 DT 打光，敌方主基地也没受损，就 revert 这条规则，或者改成更早做反隐。

从这个批次播种的新批次会把这段预测作为 `previous_prediction` 传给第一次 review（`_prior_prediction` 现在会顺着 `seed_source` 往回找）。

单轮消耗（本批次的实测）：
- 局内：两局共 105k input / 8k output tokens，38 次调用。单次 2.2–12.8 秒，中位数 4.3 秒。
- 复盘：586k input / 12.8k output tokens，180 秒。当时还没开 prompt caching。现在已经开启，实测每轮能从缓存读到上一轮的全部前缀（约 80% 以上），按缓存读取价格计费。
- 时长：两局并行约 11 分钟，加上复盘约 3 分钟，每轮约 15 分钟；10 轮约 2.5 小时。

## 1.1 正式实验建议的启动方式

```powershell
& .\scripts\rsi_run.ps1 -Rounds 10 -MatchesPerRound 2 -Parallel 2 -SeedVersion matches\rsi\20261124-133626-533308\versions\v2 -Opponent PylonPuller,Locutus,Stardust-SourceRepairCandidate,McRave,BananaBrain,Steamhammer,PurpleWave,HladHammer,tscmoo-AuthorCandidate
```

- 对手：Destination 上通过兼容矩阵的 9 个 bot 全部轮换，Skynet 除外。这 9 个都已经在 RSI 下实机跑过（见 §6 的冒烟批次）。
  - PylonPuller 排在第一，这样第 1 轮第 1 局还是打它，可以检验 v2 的预测。
  - 9 个是奇数，每过一圈两两配对的组合都会错开。
  - 代价：20 局里每个 bot 只会碰到约 2 次，单个对手的信号偏弱。如果想看针对少数对手的进化，就缩小列表。
- 种子在新批次里会被重新编号为 v1；`manifest.json` 的 `seed_source` 记录了来源。
- 累计战绩（`version_history`）和上一次的预测会从种子批次接上。
- 开跑前先确认代码已提交到 git（见 §5）。

## 2. 怎么跑

在项目根目录的 PowerShell 里：

```powershell
& .\scripts\rsi_run.ps1 -Rounds 10 -MatchesPerRound 2 -Parallel 2 -SeedVersion <种子版本目录>
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Rounds` | 10 | 轮数。每轮打完 `MatchesPerRound` 局后做一次复盘 |
| `-MatchesPerRound` | 2 | 每轮局数 |
| `-Parallel` | 1 | 同一轮里同时开打的局数。2 已实机验证 |
| `-SeedVersion` | 空 | 从某个批次的 `versions/vN` 播种，作为新批次的 v1；为空则用内置种子 |
| `-NoEvolve` | 关 | 只打不复盘 |
| `-MaxFrames` | 0（即 36000） | 单局帧数上限，做短局冒烟时用 |
| `-Model` | claude-opus-5-5 | 局内和复盘共用 |
| `-LiveEffort` / `-ReviewEffort` | low / high | 推理强度 |
| `-Map` | Destination | 一个批次只用一张图 |
| `-Opponent` | PylonPuller | 可以给一个或多个（逗号分隔）。给多个时第 N 局打列表里第 `(N-1) % k` 个，所以每轮 2 局会碰到 2 个不同的对手。兼容矩阵里在这张图上没通过的对手，启动时直接报错 |

其他固定设置：
- 后端：`--backend openai --endpoint <OpenAI 兼容 /v1 地址>`（通过 `-Endpoint` 或环境变量 `RSI_API_ENDPOINT` / `OPENAI_BASE_URL` 提供）。
- 复盘：`--review-mode agentic`，`--review-timeout 180`。

环境：
- API key 从环境变量 `OPENAI_API_KEY`（或 `-ApiKeyEnv` 指定的变量）读取，不写入任何存档。不要打印它。
- Python 一律用 `py -3.12`（默认的 `python` 是 3.9，测试会失败）。
- 游戏目录在 `local.settings.json` 的 `game` 键里（这个文件不进 git）。`rsi_live` 启动时会检查它。
- 运行期间整个批次持有本机的对局锁：桌面程序和其他批次都开不了局。

停止：在终端里按 Ctrl+C。正在打的局按 `manual_stop` 记录，这一轮不复盘，批次状态记为 `manual_stop`。

## 3. 每个批次的产物

`matches/rsi/<时间戳>/`：

- `batch.json`：参数、每局结果、每轮复盘、统计、token。`state` 取值：
  - `completed`：正常跑完。
  - `manual_stop`、`model_circuit_open`、`model_unavailable`、`repeated_match_errors`、`preflight_failed`。
  - 没有 `state`：还在跑，或者进程被直接杀掉了。
- `active.json`：当前生效的版本。
- `versions/vN/`：每个版本的 `strategy.md`、`observer.py`、`tools.py`、`rules.py` 和 `manifest.json`。
- `match-N/`：
  - `match.json`：L0 结果。`policy_version` 是本局实际使用的版本，`degraded` 表示多数模型调用失败。
  - `timeline.json`：每次决策和规则触发。
  - `decision-NNN.json`：每次局内模型调用的完整请求和回复。
  - `observations.json`：每 120 帧一行，含 `situation`。
  - `runtime-errors.json`。
  - `review-trace-N.jsonl`、`review-workspace-N/`：agentic 复盘的工具调用记录和暂存文件，放在本轮最后一局的目录里。
- `evolution-log.jsonl`：复盘与版本切换的事件流。

## 4. 每轮建议检查

都从 JSON 读，不截图（见 AGENTS.md）。

1. `batch.json` 的 `rounds[-1]`：
   - `validation`：accepted / kept / reverted / rejected / skipped / model_error，以及 `from_version → to_version`。
   - `review_usage`。
2. 每局的 `match.json`：
   - `state`、`natural_outcome`、`adjudicated_outcome`。
   - `degraded` 应为 false。
   - `decision_sources.invalid` 应接近 0（修复后的预期；如果升高，先看对应的 `decision-NNN.json` 里模型回了什么）。
3. 下一轮各局的 `policy_version` 应等于上一轮的 `to_version`。这是"新版本确实被用上"的直接证据。
4. 规则触发：`timeline.json` 里 `source == "rule"` 的行，例如种子规则 `release_idle_defense` 或复盘新加的规则。
5. `runtime-errors.json` 应为空。一旦非空，说明某个策略钩子（observer、rules、tools）在实战中抛了异常，那一次调用已经回退或跳过。
6. 复盘 trace（`review-trace-N.jsonl`）：工具调用次数、`check_candidate` 是否通过，以及 diagnosis 引用的帧号在 observations 里是否真实存在。
7. 版本差异：用 `git diff --no-index versions/vN versions/vN+1` 看改了什么。

## 5. 已知问题和限制

- **代码还没提交到 git。** `app/rsi/`、`app/rsi_live.py`、测试和文档都处于未跟踪状态。正式实验前应该先提交，这样每个批次都能对应到一个确定的代码版本。`batch.json` 只记录原生层和地图的哈希，不记录 Python 代码的哈希。
- **Opus 的回复格式会漂移。** 已经观察到三种情况：理由超长（133–407 字）、先写一段分析再给代码块、先写分析再给裸 JSON。
  - 现在的处理：理由截断到 160 字并标记 `reason_truncated`；缺 `args` 时按 `{}` 处理；回复本身不是 JSON 时，如果文本里恰好只有一个 JSON 对象，就取它；prompt 也写明了只回 JSON。
  - 持续观察 `decision_sources.invalid`。
- **指挥层不能改建造顺序。** 可用按键只有 0–9 和 `-` 这些高层命令，生产和科技由 baseline 负责。
  - PylonPuller 在 Destination 上会出 DT：今天打满的 3 局里有 2 局出了 DT，历史批次 `20261123-194749-829148/match-1` 也是 DT 局。反隐出得多早取决于 baseline。
  - 复盘只能在战术上缓解，例如锁采矿、守在光炮附近、换家；评估进化效果时要把这一点算进去。
- **对手和地图都可以选，但一个批次内是固定的。** 用 `-Opponent` / `-Map` 指定，可选 `app/matches.py` 里 `OPPONENTS` 的 10 个 bot（全是神族）和 3 张地图。
  - RSI 代码里没有专门针对 PylonPuller 的逻辑，它只是默认值。
  - 在 RSI 下实机跑过的：Destination 上的 9 个对手。PylonPuller 打过整局；其余 8 个只打过 3600 帧的冒烟局，都能正常开局，telemetry 和模型调用也正常，但还没打过整局。Benzene 和 Heartbreak Ridge 在 RSI 下还没实机跑过，出生点已经从地图文件中提取出来，并通过了单元测试。
  - v17 的兼容矩阵（`validation/compatibility.json`，每组只测 360 帧短局）里，30 组"对手 × 地图"通过了 28 组。没通过的是 PurpleWave × Heartbreak Ridge 和 Skynet × Destination，这两组不要用。
  - PurpleWave 是 Java bot，会多起一个 java 进程，并行时的表现还没测过。
  - 支持按局轮换对手（`-Opponent A,B,...`）。review 的 facts 和 `version_history` 里每局都带 `opponent`。局内 payload 也带 `opponent`，所以 `strategy.md` 可以按 bot 名记录打法。
  - `rules.py` 和 `observer.py` 拿不到对手名：summary 和 situation 里没有这个字段，因此 reflex 规则不能按对手分支。
  - `_active_record` 的累计战绩不分对手：一个版本输给 2 个不同的 bot，同样算"2 败 0 胜"，keep 会被拒。
- **并行只实机测试过 2 局。** 两局都是标准速度（24 fps），没有互相拖慢。更多局并行时，先看 CPU 和帧率再说。
- **双方 bot 是固定的，同一张图上两局前期走势很像。** 但两局并不完全相同：第 2400 帧起采矿量和单位位置就有差异，出生点也是随机的。
- **批次进程如果被直接杀掉，`batch.json` 里不会留下 `state`。** 要停止请用 Ctrl+C。

## 6. 批次目录索引

均在 `matches/rsi/` 下：

| 目录 | 内容 |
|---|---|
| `20261124-142032-393026` | 对手轮换冒烟。Destination 上 8 个对手各打 1 局，两局并行，每局 3600 帧，不复盘。8 局全部 `inconclusive`，没有错误；对手与局号一一对应，payload 里也带了对手名；`state` 为 `completed` |
| `20261124-133626-533308` | **本次实机验证。** 1 轮 × 2 局并行，v1 → v2。批次是用旧代码跑的，`batch.json` 里没有 `state`（`completed` 是之后才加的），进程以退出码 0 正常结束 |
| `20261124-132204-490957` | 串行批次，暴露了 reason 超长的 bug（第 1 局 16 次决策作废 15 次）。第 2 局开打后手动停止，没有做复盘。数据不可用于评估 |
| `20261124-121211-457332` | 找不到游戏目录，3 局都在准备阶段报错（`repeated_match_errors`） |
| `20261124-agentic-offline` | agentic 复盘的离线验收，v1 → v2 |
| `20261124-offline-check` | A 阶段的离线验收：payload、shadow 回放、决策重放 |
| `20261123-*` | 修复之前的历史批次，旧版 payload，只作参考 |

## 7. 本次会话改过的文件

- 对手轮换：`app/rsi_live.py`（`_opponent_for`、`_failed_pairs`）、`app/rsi/runner.py`（payload 中的 `opponent`）、`app/rsi/evolution.py`（`version_history` 带对手，review prompt 加了一句）、`app/rsi/agentic_review.py`（结果字段带对手和地图）、`scripts/rsi_run.ps1`（`-Opponent` 接受多个、`-MaxFrames`）。
- 并行：`app/matches.py`（`playing(hold_lock=)`）、`app/rsi/adapter.py`（`hold_lock`）、`app/rsi_live.py`（`--parallel`、`_play_wave`、批次级锁、`state: completed`）、`scripts/rsi_run.ps1`（`-Parallel`、`-SeedVersion`、`-NoEvolve`）。
- 局内决策容错：`app/rsi/runner.py`（reason 截断、补默认 `args`、prompt 加写格式要求）、`app/rsi/model_client.py`（`_decode` 从文本里取出唯一的 JSON 对象）。
- 复盘：`app/rsi/agentic_review.py`（prompt caching 断点）、`app/rsi/evolution.py`（`_prior_prediction` 跟着种子追溯）。
- 测试：`validation/test_rsi_resilience.py`、`test_rsi_runner.py`、`test_rsi_evolution.py`、`test_rsi_agentic.py`。共 72 个，`py -3.12 -m unittest validation.test_rsi_resilience validation.test_rsi_runner validation.test_rsi_evolution validation.test_rsi_agentic validation.test_rsi_situation validation.test_rsi_validator` 全部通过。
- `local.settings.json`：游戏目录，不进 git。
