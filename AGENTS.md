# 项目工作约束

## 测试优先使用日志，默认禁止屏幕捕获

- 用户已明确要求避免黄框反复出现：后续测试默认只读日志、游戏数据、回放元数据和程序状态。
- 不为自动验证启动桌面或游戏画面的截图、录屏、屏幕共享、Windows.Graphics.Capture 会话或 `WindowRecorder.exe` 的捕获功能。
- 不调用 Computer Use、CUA、Sky 等工具获取屏幕或窗口画面；优先使用命令行、文件、进程状态和程序提供的接口。
- 原生对局和回放验证可以启动游戏，但验证依据应为 BWAPI 状态、JSON/JSONL 日志、单位坐标、任务记录和回放帧号，不以截图或录屏为默认步骤。
- 只有用户明确要求本次截图、录屏或电脑画面操作时，才可为该次请求启用相应能力；“测试一下”“验证效果”不等于要求捕获屏幕。
- 正常构建必须保持 `COMMANDER_ENABLE_SCREEN_CAPTURE=OFF`；不得为了运行旧测试自行开启该选项。
- 不通过隐藏系统捕获指示、修改系统隐私设置或后台循环结束进程来绕过这条约束。
- 若捕获来自本项目之外的其他会话或应用，应说明范围；不要擅自修改全局 Codex 配置、停用插件或关闭其他应用。

## 保持底层基线不变

- 不修改 `commander_desktop/baseline/Bot.cpp` 和 `commander_desktop/baseline/Baseline.dll`。
- 对战控制和复盘功能应在上层实现；需要核验冻结资产时使用 `commander_desktop/app/matches.py --validate`。
