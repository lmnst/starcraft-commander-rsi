# 冻结代码基线

版本：`v6-fix-r1-20261110`。

- `Bot.cpp`：完整基线源码。
- `Baseline.dll`：与清单对应的预编译x86运行文件。
- `manifest.json`：两份文件的SHA256；启动时会再次校验。

它们从现有已验证版本原样复制，没有为了发布而改写或重新编译基线。当前十键、避险、增援、目标选择与回放功能由独立的CommanderLayer／ReplayControl提供。

直接使用者请下载仓库Releases的Windows运行包，里面同时包含这份基线、指挥台、固定版本对手及必要运行环境；不需要自行编译。仍需自行安装StarCraft: Brood War 1.16.1。GitHub的Code ZIP是源码快照，不等于完整运行包。
