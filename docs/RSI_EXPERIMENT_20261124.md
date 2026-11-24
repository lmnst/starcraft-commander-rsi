# RSI 正式实验记录（2026-11-24）

所有数字取自批次目录里的 JSON（`batch.json`、`match-N/match.json`、`timeline.json`、`observations.json`、`review.json`、`review-trace-N.jsonl`），不看画面。

## 批次

实验分三段。第 1 轮在批次 A 里跑完，随后停下修复局内回复解析。B 跑了 2 轮，第 3 轮打到一半停下，修复 PurpleWave 的启动问题。剩下的 7 轮在 C 里跑。

| 段 | 目录 | 代码 | 种子 | 轮数 | 状态 |
|---|---|---|---|---|---|
| A | `matches/rsi/20261124-143925-255737` | `777e1b9` | `20261124-133626-533308/versions/v2` | 1 轮完成，第 2 轮开局即停 | `manual_stop` |
| B | `matches/rsi/20261124-145321-768219` | `3859a40` | `20261124-143925-255737/versions/v1` | 2 轮完成，第 3 轮中止（B-5 对手 Java 崩溃无效、B-6 手动停止） | `manual_stop` |
| C | `matches/rsi/20261124-153350-624112` | `87338a4` | `20261124-145321-768219/versions/v3` | 7 | 运行中 |
| 冒烟 | `matches/rsi/20261124-152823-104934` | `87338a4` 的改动（提交前） | B/v3 | 1 轮 × 2 局，4800 帧，不复盘 | `completed`，只用于验证 PurpleWave 修复 |

共同设置：
- 地图 Destination；模型 claude-opus-5-5（局内 low，复盘 high，agentic）；每轮 2 局并行。
- 开跑前 72 个 RSI 单测通过，`app/matches.py --validate` 通过。
- 对手按局轮换，第 N 局打列表第 `(N-1) % 9` 个。B 的列表从 Stardust 起排，所以和 A 连起来仍是同一个循环：PylonPuller、Locutus、Stardust-SourceRepairCandidate、McRave、BananaBrain、Steamhammer、PurpleWave、HladHammer、tscmoo-AuthorCandidate。

启动命令：

```powershell
# A
& .\scripts\rsi_run.ps1 -Rounds 10 -MatchesPerRound 2 -Parallel 2 -SeedVersion matches\rsi\20261124-133626-533308\versions\v2 -Opponent PylonPuller,Locutus,Stardust-SourceRepairCandidate,McRave,BananaBrain,Steamhammer,PurpleWave,HladHammer,tscmoo-AuthorCandidate
# B
& .\scripts\rsi_run.ps1 -Rounds 9 -MatchesPerRound 2 -Parallel 2 -SeedVersion matches\rsi\20261124-143925-255737\versions\v1 -Opponent Stardust-SourceRepairCandidate,McRave,BananaBrain,Steamhammer,PurpleWave,HladHammer,tscmoo-AuthorCandidate,PylonPuller,Locutus
# C
& .\scripts\rsi_run.ps1 -Rounds 7 -MatchesPerRound 2 -Parallel 2 -SeedVersion matches\rsi\20261124-145321-768219\versions\v3 -Opponent PurpleWave,HladHammer,tscmoo-AuthorCandidate,PylonPuller,Locutus,Stardust-SourceRepairCandidate,McRave,BananaBrain,Steamhammer
```

### 中途停止与修复 1：局内回复解析（A 第 1 轮之后）

- 现象：A 第 2 局（Locutus）23 次局内调用里有 3 次作废（`decision-007`、`-008`、`-022`），错误都是 `JSONDecodeError: Extra data`。
- 原因：模型先回一个带重复 `"target": null` 的对象，接一行 "Fix: single target field." 之类的话，再回一个修正后的对象。原来的 `_decode` 只接受文本里恰好有一个对象，遇到两个就作废。
- 影响：这 3 次两个对象都是 `keep_current`，所以这局的行为不受影响，但作废率 13%。
- 修复：`3859a40`，文本里有多个对象时取最后一个。用这 3 条真实回复重新解码都得到 `keep_current`；72 个单测通过。
- 停止时机：等 A 第 1 轮复盘写完、第 2 轮刚开局时按 Ctrl+C。A 的 match-3 停在第 21 帧，match-4 停在第 4 帧，都是 `manual_stop`，不计入评估。
- 续跑的代价：`version_history` 只往回追一层种子。B 里 v1 的累计战绩来自 A 的 4 局（1 胜、1 次 `cutoff_loss`、2 局 `manual_stop`），不含 133626 批次同一份策略打的 0 胜 2 负。`previous_prediction` 正确接上了 A 第 1 轮 review 的 expectation。

### 中途停止与修复 2：PurpleWave 启动即崩溃（B 第 3 轮中）

- 现象：B-5（PurpleWave，v3）9767 帧自然胜利，但整局敌方只出现过 Nexus 和 Probe（`seen_types` 只有这两种），没有任何建筑或部队。
- 原因：`match-5/opponent/java.log` 显示 PurpleWave 启动即抛 `InaccessibleObjectException: Unable to make field long java.nio.Buffer.address accessible: module java.base does not "opens java.nio"`。`app/matches.py` 找 java 的顺序是 `STARCRAFT_JAVA` → `runtime/jre` → LAB 的 `local.settings.json` → PATH 上的 `java`。本机前三个都没有，用的是 PATH 上的 JDK 21，而 JBWAPI 在 Java 16+ 上需要打开 `java.nio`。PurpleWave 从头到尾没有控制自己的单位。
- 这个问题之前就存在：冒烟批次 `20261124-142032-393026` 的 match-2（PurpleWave）`java.log` 里是同样的异常。冒烟局只打 3600 帧、只看 `inconclusive`，所以没被发现。
- 处理：在 B-5 结束、B-6（HladHammer，v3）还在打时按 Ctrl+C，避免本轮复盘吃进这场假胜利。B-6 停在 11415 帧，`manual_stop`。B 的状态为 `manual_stop`，B 第 3 轮没有复盘。
- 修复：`app/matches.py` 启动 Java 对手时给进程环境加 `JDK_JAVA_OPTIONS=--add-opens=java.base/java.nio=ALL-UNNAMED`（Java 9+ 会读这个变量，Java 8 会忽略）。离线对照：不加时 jar 启动即抛同样的异常；加上后越过这一步，开始 "Looking for an instance of StarCraft with BWAPI"。
- 修复验证（冒烟批次 `20261124-152823-104934`，PurpleWave 与 HladHammer 并行，4800 帧）：PurpleWave 的 `java.log` 没有异常，记录了 1119 帧 Pylon、1806 帧 Gateway、3141 帧第一个 Zealot、3882 帧第一个 Dragoon，4133 帧转入进攻，并侦察到我方主基地。两局 invalid 都是 0，`state` 为 `completed`。
- 评估口径：B-5 不计入任何胜负统计。但它已经写进 B 的 `batch.json`（`finished`、`win`、`policy_version` v3），从 B/v3 播种的后续批次里，`version_history` 会把它算成 v3 的 1 胜，后续复盘看到的 v3 战绩会因此偏高。我没有改 `batch.json`，因为它是实验的原始记录。

## 版本谱系

同一份策略在三个批次里的编号不同：133626/v2 = A/v1 = B/v1。

C/v1 = B/v3。

| 版本 | 来源 | 产生于 | 内容摘要 |
|---|---|---|---|
| B/v1（= A/v1 = 133626/v2） | 种子 | — | 种子规则 `release_idle_defense` + `early_commit_enemy_main`；strategy.md 写明没有反隐时不要反复按 6 或 5 |
| B/v2 | B/v1，modify / bugfix_code | 第 2 轮 | `release_idle_defense` 加条件：军队 384px 内有敌方非农民单位或 `enemy_engaged > 0` 时不解锁（新函数 `_enemies_near_army`）|
| B/v3 | B/v2，modify / distill_rule | 第 3 轮 | 新 reflex `withdraw_one_sided_fight`：baseline 姿态、军队在 mid_map 或 near_enemy、`enemy_engaged ≥ 3`、我方 `engaged = 0`、附近敌方战斗单位 ×2 ≥ 我方健康兵力时按 9，冷却 480 帧。strategy.md 加了撤退后不要在中场以少打多，以及 BananaBrain（大量龙骑）、Steamhammer（在我方分矿附近放 Forge 和光炮）的注意事项 |

## 每轮结果

"轮"按实验连续编号：第 1 轮 = A 第 1 轮；第 2、3 轮 = B 第 1、2 轮；第 4 轮之后 = C 第 1 轮起。B 第 3 轮中止，不占编号。

| 轮 | 局 | 对手 | 版本 | 结果（自然 / 裁定） | 帧 | invalid / 调用 | 规则触发 | 复盘 | 复盘 token（input / cached / output） |
|---|---|---|---|---|---|---|---|---|---|
| 1 | A-1 | PylonPuller | v1 | win / — | 12061 | 0 / 16 | 无 | kept，v1 → v1 | 258,562 / 204,187 / 6,090 |
| 1 | A-2 | Locutus | v1 | unknown / cutoff_loss（stalemate_zero_units） | 11284 | 3 / 23 | 无 | （同上） | |
| 2 | B-1 | Stardust-SourceRepairCandidate | v1 | unknown / cutoff_loss（stalemate_zero_units） | 12843 | 0 / 24 | `release_idle_defense` @11400 | accepted，v1 → v2 | 361,835 / 285,469 / 4,624 |
| 2 | B-2 | McRave | v1 | unknown / cutoff_loss（stalemate_zero_units） | 14525 | 0 / 26 | 无 | （同上） | |
| 3 | B-3 | BananaBrain | v2 | unknown / cutoff_loss（stalemate_zero_units） | 13566 | 0 / 21 | 无 | accepted，v2 → v3 | 616,971 / 542,379 / 14,531 |
| 3 | B-4 | Steamhammer | v2 | unknown / cutoff_loss（stalemate_one_army_or_less） | 12844 | 0 / 20 | 无 | （同上） | |
| 4 | B-5 | PurpleWave | v3 | win / —（**无效**，对手 Java 崩溃） | 9767 | 0 / 8 | `early_commit_enemy_main` @9360 | `manual_stop`（未复盘） | — |
| 4 | B-6 | HladHammer | v3 | unknown / —（`manual_stop`） | 11415 | 0 / 17 | 无 | （同上） | |

## 各轮细节

### 第 1 轮（A 第 1 轮）

§4 清单：

| 项 | 结果 |
|---|---|
| `validation` | `kept`，v1 → v1，decision = keep，distillation = none |
| `review_usage` | input 258,562，cached 204,187（79%），output 6,090，76 秒。prompt caching 第一次在完整批次里生效 |
| `degraded` | 两局都是 false；`live_model_failures.failed` 都是 0 |
| `decision_sources.invalid` | A-1 为 0；A-2 为 3（原因和修复见上文） |
| `policy_version` | 两局都是 v1 |
| 规则触发 | 两局 `source == "rule"` 都是 0 |
| `runtime-errors.json` | 两局都为空 |
| 复盘 trace | 5 轮、7 次工具调用（read_policy 2、get_timeline 1、get_frames 2、run_analysis 1、submit 1），submit 被接受。决定是 keep，不产生候选，所以没有 `check_candidate`。diagnosis 引用的帧号（7080、8040、8400、9120、9840、10200、10800、10920、11280、11400）都能在对应局的 observations 里找到 |
| `previous_prediction` | 拿到了，内容是 133626 批次写给 `early_commit_enemy_main` 的预测。review 判定本轮无法检验（见下） |
| 两局的对手 | review 的 diagnosis 分别写了 "Match 1 vs PylonPuller" 和 "Match 2 vs Locutus" |

A-1（PylonPuller，胜）：
- 整局没出现 DT（observations 里 `visible_enemies` 和 `visible_undetected_enemies` 都没有 Dark Templar）。
- `early_commit_enemy_main` 没有触发。原因是 LLM 抢先了：6840 帧的决策按了 6（回执 `applied`），理由是"7 个健康 Zealot 在已知敌方主基地附近，看不到敌方部队，趁 DT 科技出来之前打"。部队 7080 帧进入 near_enemy 时健康兵力只有 7，低于规则门槛 8；之后姿态变成 `attack`，规则要求的 `stance == baseline` 就不再满足。
- 部队一路压到离敌方建筑 19–58px，约 10920 帧起 situation 里不再有已知的敌方 Nexus；10920 帧按 5（回执 `expansion_unknown`），11520 帧按 `-` 回到 baseline，12061 帧自然胜利。
- 所以上一条预测的前半句（规则在 DT 前触发）这轮没机会检验；后半句的条件（进攻任务到达、敌方主基地受损）由 LLM 的 6 达成了。

A-2（Locutus，cutoff_loss）：
- 8040 帧起 Locutus 的龙骑在中场和我方以 Zealot 为主的部队交战，敌方龙骑从 2 个涨到 7 个。8640 帧按 9（撤退），8880 帧按 0（锁防），回执都是 `applied`。
- 敌方部队继续增长，我方部队 9840 帧打光，工人 9840–10800 帧从 23 降到 0，11280 帧以 `stalemate_zero_units` 截断。
- 军队一直没到 near_enemy，规则没有触发条件。

review 的 expectation（写给下一次 review）：再打 PylonPuller 时应该在 8000 帧前按 6 并获胜；打 Locutus 如果部队和工人在 11300 帧前又被 15+ 龙骑打光，就在 strategy.md 里加一条 Locutus 的应对；如果 `early_commit_enemy_main` 又是 0 次触发，也要复查这条规则。注意：按轮换，下一次碰到 PylonPuller 是第 5 轮，碰到 Locutus 是第 6 轮。

### 第 2 轮（B 第 1 轮）

| 项 | 结果 |
|---|---|
| `validation` | `accepted`，v1 → v2，decision = modify，distillation = bugfix_code，改了 `rules.py` 的 `_enemies_near_army`（新增）和 `decide_reflex` |
| `review_usage` | input 361,835，cached 285,469（79%），output 4,624，60 秒 |
| `degraded` / invalid | 两局都是 false / 0。修复后的第一轮，invalid 为 0 |
| `policy_version` | 两局都是 v1 |
| 规则触发 | B-1：`release_idle_defense` 在 11400 帧按 `-`。B-2：无 |
| `runtime-errors.json` | 两局都为空 |
| 复盘 trace | 5 轮、7 次工具调用（read_policy 2、get_frames 1、run_analysis 1、write_policy 1、check_candidate 1、submit 1）。`check_candidate` passed：候选版本在 B-1 的 11400 帧不再解锁，其余唤醒帧不变 |
| `previous_prediction` | 拿到了 A 第 1 轮的 expectation。review 认为"被 15+ 龙骑打光"这一条应验了（Stardust 17 个龙骑、McRave 19 个单位），Locutus 那条因为对手不同无法检验；`early_commit_enemy_main` 又是 0 次触发 |

B-1（Stardust，cutoff_loss）：
- LLM 按键：7560 帧按 9，8040 帧按 0，9120 帧按 `-`，9600 帧按 0。
- 11400 帧，`release_idle_defense` 按 `-` 解锁。这一帧锁防 1440 帧、`frames_since_enemy_near_base` 2040，但军队 384px 内有 14 个龙骑、`enemy_engaged` 为 10，军队 8 个。
- 解锁后军队 120 帧内 8 → 4，11640 帧剩 2，11760 帧为 0；11520 帧 LLM 按 0 重新锁防；12843 帧 `stalemate_zero_units`。
- 解锁前军队已经在掉（11160 帧 11 个，11280 帧 9 个），所以"解锁导致崩盘"是 review 的假设，数据只说明解锁发生在交战中。

B-2（McRave，cutoff_loss）：
- LLM 按了 6、9 ×2、7、0。review 提到 8160 帧 LLM 自己按了 6，所以 `early_commit_enemy_main` 没机会触发。最后 `stalemate_zero_units`，14525 帧。

review 的一处错误：diagnosis 写 "v1 now has 0 wins and 2 losses in scored play"。但 payload 里 `version_history.totals.wins = 1`（A-1 胜 PylonPuller），实际是 1 胜、3 次 cutoff_loss（A-2、B-1、B-2）。这个错误不影响 bugfix 本身的依据（11400 帧的数据核对无误），但 review 对战绩的表述不可信，后续要留意。

review 的 expectation：下一轮 `release_idle_defense` 不应在军队附近有敌方战斗单位或 `enemy_engaged > 0` 时触发；对 Stardust / McRave 时，防守中不应在 `-` 后 120 帧内从 8+ 掉到 4 以下；如果仍然零胜、军队在约 12000 帧前打光，就 revert 或改兵种 / 科技。下一轮对手是 BananaBrain 和 Steamhammer。

### 第 3 轮（B 第 2 轮）

| 项 | 结果 |
|---|---|
| `validation` | `accepted`，v2 → v3，decision = modify，distillation = distill_rule，改了 `rules.py`（新增 `_one_sided_withdraw`，`decide_reflex` 调用它）和 `strategy.md` |
| `review_usage` | input 616,971，cached 542,379（88%），output 14,531，183 秒（接近 180 秒的复盘超时，但正常 submit） |
| `degraded` / invalid | 两局都是 false / 0 |
| `policy_version` | 两局都是 v2，确认上一轮的 v2 被用上 |
| 规则触发 | 两局都是 0 |
| `runtime-errors.json` | 两局都为空 |
| 复盘 trace | 9 轮、11 次工具调用（read_policy 2、run_analysis 4、get_frames 1、write_policy 2、check_candidate 1、submit 1）。`check_candidate` passed，候选版本在 B-3 回放里多触发 1 次 reflex |
| `previous_prediction` | 拿到了。上一条预测"`release_idle_defense` 不会在交战中触发"成立：两局这条规则都没触发，唯一一次 `-` 是 B-4 的 LLM 在 8524 帧按的（当时附近没有敌人）。"零胜且军队约 12000 帧前打光就 revert 或换方向"这一条也应验了，review 选择换方向而不是 revert |

B-3（BananaBrain，cutoff_loss）：
- 军队 8640 帧到达峰值（12 个，全部健康）。9000 帧到 near_enemy，附近 8 个龙骑，`enemy_engaged` 7、我方 `engaged` 0；9120 帧健康 7，9240 帧健康 6，LLM 这时按 9。
- 之后 LLM 按 7，10080 帧剩 7 个；11520 帧只剩 2 个，附近 10 个龙骑 2 个 Zealot。11520 帧起出现隐形单位（`visible_undetected_enemies` 非空）。13566 帧 `stalemate_zero_units`。
- review 写的是"9000 帧 12 个健康"，observations 里 9000 帧是 12 个军队、11 个健康，小偏差。

B-4（Steamhammer，cutoff_loss）：
- Steamhammer 在离我方主基地（1056, 272）约 1000px 的位置放了 Forge 和 3 个光炮（1936–2176, 608–640）。9120 帧军队走进光炮线（3 光炮 + 3 龙骑），`engaged` 0，健康从 7（9000 帧）降到 3（9240 帧）。
- LLM 按了 9 ×2、0 ×2、`-` ×1。10800 帧只剩 1 个单位，12844 帧以 `stalemate_one_army_or_less` 截断。

review 的 expectation：下一轮 `withdraw_one_sided_fight` 应在第一次出现"离家、`enemy_engaged ≥ 3`、`engaged = 0`"后 120 帧内触发；触发后 240 帧内健康兵力损失不超过 3；至少一局军队在 12000 帧后还剩 1 个以上。否则 revert 到 v2 或改科技 / 兵种。review 的 prediction_check 里还写了 "There is no version_history to revert to"，这和事实不符（v3 的父版本 v2、v2 的父版本 v1 都在），属于 review 的表述错误。

下一轮对手 PurpleWave（Java bot，第一次打满整局，也是第一次和另一局并行）和 HladHammer。

### 第 4 轮（B 第 3 轮，中止，无复盘）

| 局 | 对手 | 版本 | 结果 | 说明 |
|---|---|---|---|---|
| B-5 | PurpleWave | v3 | finished / win，9767 帧 | **无效**：对手 Java 客户端启动即崩溃，整局没有行动（见上文"修复 2"） |
| B-6 | HladHammer | v3 | manual_stop，11415 帧 | 为阻止复盘吃进 B-5 而停止 |

- B-5 里 `early_commit_enemy_main` 第一次实战触发：9360 帧按 6（13 个健康单位、离敌方建筑 503px、只看到 Nexus 和 3 个 Probe）。9600 帧部队距敌方建筑 151px，9720 帧已知敌方建筑变成 0。规则机制本身按设计工作，但对手不动，这次触发不能说明规则有效。
- 两局 invalid 都是 0，runtime-errors 为空。PurpleWave 那局 9767 帧用了 438 秒，与 HladHammer 那局并行时帧率正常（开局约 5 分钟时分别在 7680 和 7080 帧）。

## 结论

（实验进行中）
