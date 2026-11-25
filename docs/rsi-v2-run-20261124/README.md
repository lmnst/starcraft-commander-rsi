# RSI v2 通宵自进化实验归档（`20261124-224218-659920`）

本目录归档了 RSI v2 首次 24 轮 96 局通宵自进化运行（`v1` → `v8`，`c1`–`c23`，零崩溃、平均 FPS ~23.8）的完整纯文本产物与复盘分析（不含二进制 `.dll` 与大体积录像文件）。

## 快速导航

- **[POSTMORTEM.md](./POSTMORTEM.md)** — 6 方向彻底复盘报告（进化真伪、裁判漏洞、模型改写行为、星际实战死因与底座天花板、16 个淘汰版本捞补丁清单、RSI v3 升级方案）
- **[PROGRESS.md](./PROGRESS.md)** — 通宵 24 轮 96 局战绩看板与版本谱系
- **[v1_to_v8.diff](./v1_to_v8.diff)** — 初始基线 `v1` 到最终 Champion `v8` 的累计代码与配置 diff（+120 行 C++，5 个配置键）
- **[candidates/SUMMARY.md](./candidates/SUMMARY.md)** — 全部 23 个挑战者（`c1`–`c23`）的假设、判定结果与逐一 `patch.diff`
- **[versions/v8/MacroBot.cpp](./versions/v8/MacroBot.cpp)** & **[build_config.json](./versions/v8/build_config.json)** — 最终 Champion `v8` 完整源码
- **[experiment-ledger.json](./experiment-ledger.json)** — 全量 L0 评分与里程碑账本 JSON

## 相关进化引擎源码

- [app/rsi/v2_loop.py](../../app/rsi/v2_loop.py) — RSI v2 主进化循环与 Agentic Review 工具链
- [app/rsi/scoring.py](../../app/rsi/scoring.py) — L0 防龟缩评分与 Champion/Challenger 裁判逻辑
- [app/rsi/cpp_gate.py](../../app/rsi/cpp_gate.py) — `MacroBot.cpp` 静态安全门禁与隔离 MSVC 编译
- [app/rsi/ledger.py](../../app/rsi/ledger.py) — 跨版本账本与 `PROGRESS.md` 渲染
