# RSI v2 通宵自进化进度看板（PROGRESS）

- **更新时间 (UTC)**：`2026-11-25T04:16:55.701251+00:00`
- **当前 Champion**：`v8`（连续 Reject 次数：`2`）
- **核心固定对手池（无 PurpleWave）**：`PylonPuller, Steamhammer, BananaBrain`
- **已完成轮次**：`24`

## 1. 当前 Champion 累计成绩（纯 L0 指标）

| 对手 | 累计局数 | 胜 / 负 / 平 | 胜率 | 平均 L0 分 | 平均 FPS |
|---|---|---|---|---|---|
| PylonPuller | 1 | 1 / 0 / 0 | 100% | 90.7 | 23.7 |
| Steamhammer | 2 | 0 / 2 / 0 | 0% | 41.4 | 23.8 |
| BananaBrain | 2 | 0 / 2 / 0 | 0% | 57.7 | 23.8 |

## 2. 版本进化谱系（含 Promoted 与 Rejected）

| 版本 | 父版 | 状态 | 改动文件（C++行数） | 结论原因 | 各对手战绩与均分（L0） |
|---|---|---|---|---|---|
| `v1` | `—` | `superseded_champion` | — | `—` | PylonPuller:1W/1L (68.6); Steamhammer:0W/2L (43.8); BananaBrain:0W/1L (49.7) |
| `c1` | `v1` | `promoted_as_v2` | MacroBot.cpp, build_config.json (Δ34L) | `win_rate_improved` | PylonPuller:1W/0L (93.2); Steamhammer:0W/1L (63.7); BananaBrain:0W/1L (52.5) |
| `v2` | `v1` | `superseded_champion` | MacroBot.cpp, build_config.json (Δ34L) | `win_rate_improved` | PylonPuller:1W/1L (69.4); Steamhammer:0W/2L (55.0); BananaBrain:0W/2L (54.6) |
| `c2` | `v2` | `rejected` | MacroBot.cpp, build_config.json (Δ17L) | `win_rate_lower` | PylonPuller:0W/1L (36.6); Steamhammer:0W/1L (64.4); BananaBrain:0W/1L (41.0) |
| `c3` | `v2` | `rejected` | MacroBot.cpp, build_config.json (Δ18L) | `regression_on_PylonPuller` | PylonPuller:0W/1L (50.1); Steamhammer:0W/1L (4.3); BananaBrain:0W/1L (39.9) |
| `c4` | `v2` | `promoted_as_v3` | MacroBot.cpp (Δ29L) | `win_rate_improved` | PylonPuller:1W/0L (91.5); Steamhammer:0W/1L (53.3); BananaBrain:0W/1L (42.7) |
| `v3` | `v2` | `superseded_champion` | MacroBot.cpp (Δ29L) | `win_rate_improved` | PylonPuller:1W/1L (74.6); Steamhammer:0W/1L (53.3); BananaBrain:0W/2L (46.7) |
| `c5` | `v3` | `rejected` | MacroBot.cpp, build_config.json (Δ7L) | `win_rate_lower` | PylonPuller:0W/1L (13.8); Steamhammer:0W/1L (20.8); BananaBrain:0W/1L (11.5) |
| `c6` | `v3` | `promoted_as_v4` | MacroBot.cpp (Δ14L) | `win_rate_improved` | PylonPuller:0W/1L (45.0); Steamhammer:1W/0L (93.5); BananaBrain:0W/1L (15.7) |
| `v4` | `v3` | `superseded_champion` | MacroBot.cpp (Δ14L) | `win_rate_improved` | PylonPuller:0W/1L (45.0); Steamhammer:1W/1L (72.9); BananaBrain:0W/1L (15.7) |
| `c7` | `v4` | `promoted_as_v5` | MacroBot.cpp (Δ15L) | `win_rate_improved` | PylonPuller:0W/1L (71.3); Steamhammer:1W/0L (93.4); BananaBrain:0W/1L (45.5) |
| `v5` | `v4` | `superseded_champion` | MacroBot.cpp (Δ15L) | `win_rate_improved` | PylonPuller:0W/2L (65.8); Steamhammer:1W/1L (52.9); BananaBrain:0W/2L (52.4) |
| `c8` | `v5` | `rejected` | MacroBot.cpp (Δ14L) | `win_rate_lower` | PylonPuller:0W/1L (46.6); Steamhammer:0W/1L (53.6); BananaBrain:0W/1L (46.5) |
| `c9` | `v5` | `rejected` | MacroBot.cpp (Δ7L) | `win_rate_lower` | PylonPuller:0W/1L (55.8); Steamhammer:0W/1L (43.0); BananaBrain:0W/1L (17.4) |
| `c10` | `v5` | `promoted_as_v6` | MacroBot.cpp, build_config.json (Δ10L) | `win_rate_improved` | PylonPuller:0W/1L (60.5); Steamhammer:1W/0L (88.4); BananaBrain:0W/1L (19.5) |
| `v6` | `v5` | `superseded_champion` | MacroBot.cpp, build_config.json (Δ10L) | `win_rate_improved` | PylonPuller:1W/2L (67.2); Steamhammer:1W/2L (41.2); BananaBrain:0W/3L (16.3) |
| `c11` | `v6` | `rejected` | MacroBot.cpp (Δ5L) | `single_opponent_score_drop` | PylonPuller:1W/0L (82.4); Steamhammer:0W/1L (50.9); BananaBrain:0W/1L (52.0) |
| `c12` | `v6` | `rejected` | MacroBot.cpp (Δ9L) | `regression_on_PylonPuller` | PylonPuller:0W/1L (43.4); Steamhammer:1W/0L (93.6); BananaBrain:0W/1L (33.5) |
| `c13` | `v6` | `rejected` | MacroBot.cpp, build_config.json (Δ18L) | `regression_on_PylonPuller` | PylonPuller:0W/1L (50.1); Steamhammer:0W/1L (50.0); BananaBrain:0W/1L (30.6) |
| `c14` | `v6` | `rejected` | MacroBot.cpp (Δ16L) | `regression_on_Steamhammer` | PylonPuller:1W/0L (87.9); Steamhammer:0W/1L (34.2); BananaBrain:0W/1L (58.4) |
| `c15` | `v6` | `rejected` | MacroBot.cpp (Δ11L) | `regression_on_Steamhammer` | PylonPuller:0W/1L (56.4); Steamhammer:0W/1L (35.6); BananaBrain:0W/1L (12.8) |
| `c16` | `v6` | `promoted_as_v7` | MacroBot.cpp (Δ37L) | `win_rate_improved` | PylonPuller:1W/0L (89.8); Steamhammer:0W/1L (49.2); BananaBrain:0W/1L (59.2) |
| `v7` | `v6` | `superseded_champion` | MacroBot.cpp (Δ37L) | `win_rate_improved` | PylonPuller:3W/0L (85.6); Steamhammer:0W/2L (58.3); BananaBrain:0W/3L (32.9) |
| `c17` | `v7` | `rejected` | MacroBot.cpp (Δ11L) | `win_rate_lower` | PylonPuller:0W/1L (54.1); Steamhammer:0W/0L/1I (89.6); BananaBrain:0W/1L (42.9) |
| `c18` | `v7` | `rejected` | MacroBot.cpp (Δ15L) | `regression_on_PylonPuller` | PylonPuller:0W/1L (51.3); Steamhammer:1W/0L (93.8); BananaBrain:0W/1L (21.3) |
| `c19` | `v7` | `rejected` | MacroBot.cpp (Δ18L) | `regression_on_PylonPuller` | PylonPuller:0W/1L (38.6); Steamhammer:0W/1L (40.1); BananaBrain:0W/1L (48.8) |
| `c20` | `v7` | `rejected` | MacroBot.cpp, build_config.json (Δ10L) | `insufficient_improvement` | PylonPuller:1W/0L (85.2); Steamhammer:0W/1L (56.5); BananaBrain:0W/1L (24.9) |
| `c21` | `v7` | `promoted_as_v8` | MacroBot.cpp, build_config.json (Δ7L) | `score_improved` | PylonPuller:1W/0L (90.7); Steamhammer:0W/1L (51.2); BananaBrain:0W/1L (60.5) |
| `v8` | `v7` | `champion` | MacroBot.cpp, build_config.json (Δ7L) | `score_improved` | PylonPuller:1W/0L (90.7); Steamhammer:0W/2L (41.4); BananaBrain:0W/2L (57.7) |
| `c22` | `v8` | `rejected` | MacroBot.cpp (Δ8L) | `win_rate_lower` | PylonPuller:0W/1L (39.6); Steamhammer:0W/1L (32.5); BananaBrain:0W/1L (39.1) |
| `c23` | `v8` | `rejected` | MacroBot.cpp, build_config.json (Δ13L) | `win_rate_lower` | PylonPuller:0W/1L (44.1); Steamhammer:0W/1L (62.9); BananaBrain:0W/1L (59.4) |

## 3. 逐轮记录

| 轮次 | 测试对象 | 局号 | 判定 | 下一轮提案 |
|---|---|---|---|---|
| R1 | `v1` | [1, 2, 3, 4] | `baseline` (—) | `c1` (staged_challenger) |
| R2 | `c1` | [5, 6, 7, 8] | `promoted` (win_rate_improved) | `c2` (staged_challenger) |
| R3 | `c2` | [9, 10, 11, 12] | `rejected` (win_rate_lower) | `c3` (staged_challenger) |
| R4 | `c3` | [13, 14, 15, 16] | `rejected` (regression_on_PylonPuller) | `c4` (staged_challenger) |
| R5 | `c4` | [17, 18, 19, 20] | `promoted` (win_rate_improved) | `c5` (staged_challenger) |
| R6 | `c5` | [21, 22, 23, 24] | `rejected` (win_rate_lower) | `c6` (staged_challenger) |
| R7 | `c6` | [25, 26, 27, 28] | `promoted` (win_rate_improved) | `c7` (staged_challenger) |
| R8 | `c7` | [29, 30, 31, 32] | `promoted` (win_rate_improved) | `c8` (staged_challenger) |
| R9 | `c8` | [33, 34, 35, 36] | `rejected` (win_rate_lower) | `c9` (staged_challenger) |
| R10 | `c9` | [37, 38, 39, 40] | `rejected` (win_rate_lower) | `c10` (staged_challenger) |
| R11 | `c10` | [41, 42, 43, 44] | `promoted` (win_rate_improved) | `c11` (staged_challenger) |
| R12 | `c11` | [45, 46, 47, 48] | `rejected` (single_opponent_score_drop) | `c12` (staged_challenger) |
| R13 | `c12` | [49, 50, 51, 52] | `rejected` (regression_on_PylonPuller) | `c13` (staged_challenger) |
| R14 | `c13` | [53, 54, 55, 56] | `rejected` (regression_on_PylonPuller) | `c14` (staged_challenger) |
| R15 | `c14` | [57, 58, 59, 60] | `rejected` (regression_on_Steamhammer) | `c15` (staged_challenger) |
| R16 | `c15` | [61, 62, 63, 64] | `rejected` (regression_on_Steamhammer) | `c16` (staged_challenger) |
| R17 | `c16` | [65, 66, 67, 68] | `promoted` (win_rate_improved) | `c17` (staged_challenger) |
| R18 | `c17` | [69, 70, 71, 72] | `rejected` (win_rate_lower) | `c18` (staged_challenger) |
| R19 | `c18` | [73, 74, 75, 76] | `rejected` (regression_on_PylonPuller) | `c19` (staged_challenger) |
| R20 | `c19` | [77, 78, 79, 80] | `rejected` (regression_on_PylonPuller) | `c20` (staged_challenger) |
| R21 | `c20` | [81, 82, 83, 84] | `rejected` (insufficient_improvement) | `c21` (staged_challenger) |
| R22 | `c21` | [85, 86, 87, 88] | `promoted` (score_improved) | `c22` (staged_challenger) |
| R23 | `c22` | [89, 90, 91, 92] | `rejected` (win_rate_lower) | `c23` (staged_challenger) |
| R24 | `c23` | [93, 94, 95, 96] | `rejected` (win_rate_lower) | `—` (final_round_no_review) |
