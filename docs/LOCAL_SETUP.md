# 本地运行与打包

## 普通使用者

下载GitHub Releases的Windows x64运行包，完整解压后双击 `星际指挥台.exe`。无需安装Python或Java。电脑需已有StarCraft: Brood War 1.16.1；首次启动选择其安装目录。程序只读取该目录，并在自己的 `matches/`、`playback/` 内创建对局和回放实例，不替换已安装的游戏文件。

运行包不含游戏本体、个人对局、回放媒体和本机配置。`local.settings.json` 保存所选游戏目录，`preferences.json` 保存界面选择，两者均只留在本机。安装路径可含空格和中文；请把整包放在可写目录。

Skynet为旧版兼容对手，依赖Visual C++ 2008 x86。其他原生模块所需的当前x86 CRT随包部署。已知对手组合限制保留在兼容性清单中。

## 从源码运行

需要Windows x64、带Tkinter的Python 3.12，以及独立准备的游戏、基线及对手文件。运行包已按以下布局包含这些运行组件；源码包有意排除二进制和个人数据。

- `baseline/Bot.cpp`、`baseline/Baseline.dll`，须匹配基线清单。
- `opponents/<名称>/bwapi-data/`，须匹配 `opponents/recorded-roster.json`。
- `build/` 的CommanderLayer、ReplayControl、WideView、ResultsWatcher、LegacyStorm DLL，以及CommanderLocalPC.snp。
- `runtime/engine/` 的injectory.x86.exe、BWAPI.dll、BWAPI-3.7.4.dll及 `crt/`。
- `runtime/maps/` 的三张固定地图；`runtime/cnc-ddraw/` 的ddraw.dll和xbr shader；`runtime/jre/` 的x86 Java 8。
- `vendor/resolution-expander/ResolutionExpander.dll`，须匹配代码中的固定SHA256。

```powershell
python -B app/desktop.pyw
```

也支持 `STARCRAFT_GAME`、`STARCRAFT_LAB`、`STARCRAFT_DISPLAY`、`STARCRAFT_JAVA` 环境变量。源码保留已有开发环境的实验目录回退；发布包优先使用包内组件和用户选择的游戏目录，不依赖开发者的E盘路径。

## 编译原生模块

打开MSVC **x86 Native Tools Command Prompt**，准备BWAPI SDK、BWEM源码，以及BWAPI SNP的Util/Storm源码：

```bat
cmake -S app -B build -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DSTARCRAFT_LAB=E:/StarCraftAI -DCOMMANDER_ENABLE_SCREEN_CAPTURE=OFF
cmake --build build
```

可分别覆盖 `BWAPI_ROOT`、`BWEM_ROOT`、`TRANSPORT_UPSTREAM`、`Python3_EXECUTABLE`。导出文件 `app/Candidate.def` 已包含在仓库内，不使用开发者电脑上的外部路径。构建不修改冻结基线。

## 生成独立运行包

```powershell
python -m pip install pyinstaller
python scripts/package-release.py --desktop-source E:/StarCraft_llmteam/commander_desktop --lab E:/StarCraftAI --display E:/StarCraft_llmteam/human_commander_local/vendor/cnc-ddraw-v7.1
```

参数明确指定本机已验证组件的来源，不能把游戏目录当成发布根目录。脚本只取白名单运行文件，不复制StarCraft.exe、MPQ游戏数据、录像、视频、个人设置和对局缓存。新包输出到 `dist/StarCraft-Commander/`；输出目录已存在时会拒绝合并，避免误打包测试缓存。

原生模块使用源码仓库的构建结果，Python/Tk使用PyInstaller目录形式打包。发布前执行exe的 `--self-check --game-dir <游戏目录> --output <报告路径>`；测试报告不应写进最终分发包。最终ZIP按 `PACKAGE_MANIFEST.json` 的文件清单封装，因此在打包后实机验证产生的对局缓存不会进入ZIP。
