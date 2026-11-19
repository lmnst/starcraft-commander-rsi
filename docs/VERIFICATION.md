# 验证记录

## v17同步（2026-11-19）

- CommanderLayer、策略头文件和回放核心同步本机最新版；保留发布包的游戏目录选择、自带Python/Tk及Java和包内运行库寻址。
- 包含v13观察者随队和反隐保护、v14原地集结、v15就近警戒和共同避险、v16主基地区域清场，以及窗口焦点管理。
- v17原生验证通过Windows消息钩子执行2、8、9、0、-、8；覆盖长按去重、Alt+8与小键盘减号不触发人工指令。0保留采矿锁，-解除全部人工任务；2401帧验证中未发现下令失败。
- 游戏HUD记录已显示9撤离集结、0回防、-重置，位置和字号保持；37项回放测试含v17与v16的不同键义解释，发布仓库全部46项Python测试通过。
- 冻结Bot.cpp和Baseline.dll的SHA256保持不变；构建禁止屏幕捕获。
- v17最终ZIP逐文件校验并重新解压后，禁用开发SDK和外部Python／Java路径，自带exe启动自检通过；全局／我方回放均通过暂停、600帧前进、120帧后退重载、倍速恢复。包内组件对PylonPuller短局推进3601帧，最多14名工人、采集1202矿，在测试帧数上限主动结束。详见 `validation/portable-v17-release-acceptance.json`，SHA256为 `94e5d95c89e319472c7c5343663e12e8b27bad10df40b8bdc2255a50367ee9e5`。

规则验证记录见 `validation/top-row-v17-acceptance.json`、`detector-v13-acceptance.json`、`assembly-v14-acceptance.json`、`local-combat-v15-acceptance.json`、`main-area-v16-acceptance.json` 和 `window-management-acceptance.json`。以下v12记录保留为历史证据。

## 战斗与回放规则（2026-11-17）

当前指挥层为十键v12，基线保持v6-fix-r1-20261110。原生数据验收记录在 `validation/20261117-combat-v12-acceptance.json`，其中原机器的目录仅作历史证据引用，不是运行程序所需的路径。

- 5／6在敌方未先开火、且敌人尚在我方射程外时主动接战；之后完成原建筑目标、安全撤离。
- 7空场持续警戒，自动迎击两波敌军；探机攻击和离开时24名守军的采样位移为0，探机血盾不变。
- 丢失视野后短暂搜索、搜索到期恢复警戒、重新看到敌人后自动接战；不透视攻击。
- 两名新增龙骑士先汇合主力；重复按键保留task_id。
- 其余按键、回防分配、共同安全集结、重置均做过原生场景回归。
- Steamhammer联机回归10801帧；大厅列表快照、收发队列和独立CPU调度已验证。
- 13组C++规则测试，38项Python回放回归通过。

## 发布包

最终运行包已完成独立验证：

- 可移植CMake x86构建全部通过，保持禁止屏幕捕获。
- 清除外部Python／Java PATH，设置无效的开发SDK目录后，exe仍能自检；三张地图、十个对手、原生模块和Tk 8.6.15均成功加载。
- 全场与我方视角各实机验证：暂停、向前定位到600帧、向后重载到120帧、倍速恢复并继续推进。
- 原生指挥层和全部回放核心源码与本机现有版本逐文件字节相同；未再修改战斗或回放逻辑。
- 38项Python回放回归通过。运行包不新增回放入口等界面功能，仅做独立启动所需的配置与打包。

发布包验证结果在validation/portable-selfcheck.json、portable-replay-all.json、portable-replay-self.json。验证是当前Windows环境上的实际运行，未声称已在所有Windows电脑上测试。

不使用截图、录屏或WindowRecorder的捕获功能。对局、回放的REP及旁路日志保留在验证机器，不作为示例媒体上传。已知的旧版对手组合问题继续保留在compatibility.json，不把一次短测等同于所有系统上的长期稳定。


## 已发布运行包的实际对战补验

从GitHub已发布ZIP的新解压目录启动，禁用开发SDK回退，全部基线、地图、对手及运行库使用包内文件，游戏本体来自用户已安装的1.16.1。

Heartbreak Ridge对PylonPuller推进到预设3601帧，基线代理成功加载；工人增长到14名，已产出作战单位，累计采集1178矿。双方接收队列无溢出。此局在验证帧数上限主动结束，不将其作为自然胜负结果。记录见validation/portable-battle-acceptance.json。

基线版本v6-fix-r1-20261110的完整Bot.cpp与冻结Baseline.dll已同时补入源码仓库和运行包，SHA256保持不变。直接玩的用户仍应下载Releases运行包，而非只有源码和基线的Code ZIP。
