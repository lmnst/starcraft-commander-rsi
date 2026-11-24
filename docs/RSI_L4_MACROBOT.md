# L4：MacroBot 与 build_config.json（2026-11-24）

## 做了什么

- `app/MacroBot.cpp`：从 `baseline/Bot.cpp` 复制而来，把 37 个生产和运营阈值换成 `cfg.<name>`，运行时从 `bwapi-data/AI/build_config.json` 读取。`baseline/Bot.cpp` 和 `Baseline.dll` 没有改动，桌面版照旧使用 Baseline.dll。
- 参数 schema 在 `app/rsi/build_config.py`，包括默认值、取值范围和说明。默认值就是 Bot.cpp 里被替换掉的那些数字。
- 新增两个开关，默认都是 -1（关闭），两个都开、只开一个还是都不开，由复盘模型决定：
  - `proactive_observer_frame`：到这一帧时，即使没看到隐形单位，也走反隐科技链（气 → VR → OB 所 → OB），并像被动反隐时一样预留资源。
  - `proactive_cannon_frame`：到这一帧时，即使没看到隐形单位，也造 Forge 和基地炮台。
  - 炮台数量由 `cannons_per_base` 控制，被动和主动两条路径共用。
- 战斗逻辑 `combat()` 没有参数化，它由 0–9 键管理。

## 默认配置为什么和 Baseline 等价

`py -3.12 -m app.rsi.macrobot_check` 做三步，结果必须和 Bot.cpp 逐字节相同：

1. 删掉 `//@config` 块（读配置、写日志、include）。
2. 删掉 `/*+*/…/*-*/` 片段。检查器只接受以下几种在默认值下不起作用的形态：
   - 条件里带 `early(cfg.X)`、且 X 默认为 -1 的提前 return；
   - `|| early(cfg.X)`，同样要求 X 默认为 -1；
   - `*cfg.cannons_per_base`，默认值为 1；
   - `if(!configOk) return;`。
3. 把 `cfg.<name>` 替换成默认值。

这只能证明源码等价。DLL 的哈希和 Baseline.dll 不同：MacroBot 用本机 MSVC 19.16、STARTcraft 的 BWAPI 4.4.0 和 `openbw-readiness` 的 BWEM 编译，与 `starcraft-fix-r1-demo` 本地重编 Baseline 时用的工具链一样，但服务器原始编译环境无法确认。

## 编译

```powershell
& .\scripts\build_macrobot.ps1
```

- SDK 路径写在 `local.settings.json` 的 `macrobot_build` 里，这个文件不进 git。
- 脚本先跑等价检查，再编译出 `build/MacroBot.dll`，并在 `build/MacroBot.json` 记录源码和 DLL 的哈希。

## 对局与 RSI

- `matches.playing(..., build_config=<文本>)`：拷入 MacroBot.dll 和配置文件，通过 `COMMANDER_DELEGATE` 让 CommanderLayer 加载 MacroBot。
- `match.json` 记录 `delegate`、`delegate_sha256`、`build_config` 和 `build_config_sha256`。
- 对局结束后核对 trace 里的 `config_loaded` 是否与下发的配置一致。缺失、`config_error` 或数值不一致，都把这局记为 error，不计胜负。配置读不到时 bot 会直接离局。
- **RSI 批次现在一律使用 MacroBot**，配置取自当前版本的 `build_config.json`。如果在 RSI 里继续用 Baseline，模型改了配置也不会生效。默认配置就是 Baseline 的行为。
- 版本快照多了 `build_config.json`。从没有这个文件的旧版本播种时，会用默认值补上，`manifest.json` 里记为 `build_config_source: default`。
- 门禁规则：
  - schema 必须完整，不认识的键直接拒绝，数值必须是范围内的整数；
  - 每轮最多改 3 个值；
  - 改配置的那一轮不能同时改别的文件；
  - `distillation_type` 写 `tune_macro`；
  - 影子回放评估不了配置改动，只能靠实战检验。
- 复盘新增 `get_build_log` 工具，读取 MacroBot 写的 `astra-trace.jsonl`（build_plan、detect_step、cloak_seen、cannon_skip 等）。`run_analysis` 的 evidence 里也加了 `build_log`。

## 今天的验证

- 单测：`validation/test_macrobot.py` 15 个，加上原有 RSI 测试，共 88 个全部通过；`app/matches.py --validate` 通过。
- 不接 LLM 的实机对照：Destination 地图，对手 PylonPuller，每局 3000 帧。
  - `validation/20261124-170906-macrobot-parity`：两边前 3 个 build_plan 的类型、帧数、坐标完全相同（864 Pylon、1680 Gateway、2904 Gateway）。从 1848/1872 帧 Gateway 开工时间不同起，采矿量开始分叉；Baseline 在 2976 帧多计划了一个 Pylon，MacroBot 在 3000 帧前还没攒够钱。
  - `validation/20261124-170605-macrobot-parity`：第一次对照。MacroBot 那局只跑到约 34 帧/秒，在 55 秒的短局时限内只到了 1800 帧，报超时；调换顺序后两局都正常打满。到 1800 帧为止两边的建造计划相同。
  - `validation/20261124-171432-macrobot-switches`：观察者开关设为 1500、炮台开关设为 1200。Forge 在 2280 帧计划（排在 Pylon 和 Gateway 之后），Assimilator 在 2664 帧计划；默认配置要到 4400 帧之后或有 3 个叉才造气。3000 帧内还没到炮台和 VR。
- 还没验证：
  - 完整一局里 MacroBot 和 Baseline 的胜负分布；
  - 复盘模型实际修改配置的效果，需要等 API 恢复。
