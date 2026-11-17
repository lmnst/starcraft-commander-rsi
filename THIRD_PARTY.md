# 第三方组件与分发边界

本仓库不对第三方软件重新声明许可。发布包仅用于已安装StarCraft: Brood War 1.16.1的Windows电脑，不包含StarCraft.exe或MPQ游戏数据。

| 组件 | 来源与说明 |
| --- | --- |
| StarCraft: Brood War | Blizzard游戏，由使用者自行安装；不随包分发游戏本体。 |
| BWAPI与Local PC SNP | https://github.com/bwapi/bwapi 。SNP修改源码在vendor/local-pc-transport，保留COPYING；包内含对应构建与BWAPI运行组件。 |
| BWEM | https://github.com/N00byEdge/BWEM 。用于地图分析，链接到指挥层；按上游许可使用。 |
| cnc-ddraw | https://github.com/FunkyFr3sh/cnc-ddraw ，本地使用7.1版本。发布包包含显示DLL和所用shader，来源及许可随运行组件保留。 |
| Resolution Expander | 来源及固定版本见vendor/resolution-expander/README.md；原始DLL不改写。 |
| Python、Tcl/Tk、PyInstaller | 打包的解释器与GUI运行组件分别遵循原许可，PyInstaller带有允许分发生成程序的例外。许可文本随其组件保留。 |
| Azul Zulu Java 8 x86 | 包内runtime/jre保留LICENSE、ASSEMBLY_EXCEPTION、THIRD_PARTY_README及发行元数据。供PurpleWave使用。 |
| Microsoft C++运行库 | 当前x86 CRT按应用本地部署；旧Skynet可能需要系统已安装Visual C++ 2008 x86。 |
| 对手机器人 | 固定版本清单、身份和哈希见opponents/recorded-roster.json；包内保留其运行目录中的原有文档和配置。 |

Stardust-SourceRepairCandidate和tscmoo-AuthorCandidate属于本地候选版本，不能称作上游最新正式版。运行包包含十个留存版本；不声称覆盖所有系统与地图组合。冻结基线版本及哈希见baseline/manifest.json。

具体录像、录屏、截图、个人对局目录、本机路径设置和访问凭据均不进入运行包。原生REP回放功能完整保留，由使用者打开自己的文件。关于测试方法和验证边界，见docs/VERIFICATION.md。
