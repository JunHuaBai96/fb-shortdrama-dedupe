# FB 短剧素材批量去重工具 

> 为 Facebook / Meta 短剧出海矩阵批量生产「同母版、不同指纹」的差异化素材。

##  已有特性

- **一键启动器**：`一键去重.bat` 自动探测 Python 与 ffmpeg，缺 ffmpeg 时自动 `pip install imageio-ffmpeg`，出错有提示且不闪退。
- **GPU 硬件编码**：自动检测 NVIDIA NVENC / Intel QSV / AMD AMF，失败自动回退 CPU。
- **镜像默认关闭**：避免原片硬字幕左右颠倒。
- **字幕 cover 模式**：用模糊条盖住原片底部字幕带，方便再叠加 AI 解说字幕。
- **并发优化**：GPU 默认 4 路并发，CPU 默认 2 路。

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.9+ | 安装时勾选 Add Python to PATH；`py` / `python` / `python3` 任一可用即可 |
| ffmpeg | 任意较新版本 | 见下方「准备 ffmpeg」 |

> **注意**：微软应用商店的 `python.exe`（位于 `AppData\Local\Microsoft\WindowsApps`）只是占位程序，
> 双击它会跳转商店、命令行执行会报错，不能算装了 Python。启动器会自动跳过它。

## 四层去重原理

Meta 的原创性/去重算法看四层，单层改必被抓，本工具会同时随机化：

| 层级 | 处理项 | 去重效果 |
|---|---|---|
| 画面 | 放大 + 非对称裁切、镜像开关、微旋转、调色、噪点、锐化 | 画面指纹 |
| 音频 | 音高微变、变速补偿、音量微调、可选叠 BGM | 声纹 / 音频指纹 |
| 结构 | 起点偏移、随机变速、随机 GOP | 结构指纹 |
| 文件 | 元数据清空、随机 CRF/CQ/GOP、全新编码输出 | 文件哈希 / MD5 |

## 快速开始

### 1. 准备 ffmpeg

因为 80MB 的 ffmpeg 二进制不适合进仓库，工具不会自带。任选其一：

```bash
# 方法 A：用 imageio-ffmpeg（pip 自动下载）
pip install imageio-ffmpeg

# 方法 B：从 gyan.dev 下载 Windows 完整版，把 ffmpeg.exe 放进工具目录的 bin/ 文件夹
# https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip

# 方法 C：系统 PATH 里已有 ffmpeg 也行
```

查找顺序：`bin/ffmpeg.exe` → 系统 PATH → 环境变量 `FFMPEG_BIN` → 常见安装位置 → `imageio-ffmpeg`。

> 双击 `一键去重.bat` 时如果检测不到，会自动执行一次 `pip install imageio-ffmpeg`，无需手动处理。

### 2. 双击运行（Windows）

```
双击：一键去重.bat
```

按提示拖入视频/文件夹，其余回车用默认即可。也可以**直接把视频或文件夹拖到 bat 图标上**，跳过第一步。

### 3. 命令行（高级）

```bash
python dedupe.py -i input/ -n 5                     # 每个视频出 5 版
python dedupe.py -i a.mp4 -n 3 --mode strong        # 单个文件，强力模式
python dedupe.py -i input/ -n 5 --bgm bgm/          # 叠加 BGM 文件夹
python dedupe.py -i input/ -n 5 --bgm-vol 0.08      # 调整 BGM 音量
python dedupe.py -i input/ -n 5 --subs cover        # 模糊盖掉原片底部字幕
python dedupe.py -i input/ -n 5 --accounts list.txt   # 生成账号分配表
python dedupe.py -i input/ -n 5 --gpu cpu           # 强制 CPU
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-i` / `--input` | 视频文件或文件夹 | 必填 |
| `-o` / `--output` | 输出目录 | `output/` |
| `-n` / `--versions` | 每个视频生成版本数（1~50） | 5 |
| `--mode` | `light` / `standard` / `strong` | `standard` |
| `--bgm` | BGM 文件夹 | 无 |
| `--bgm-vol` | BGM 音量，0.01~0.30 | 0.08 |
| `--accounts` | 账号列表 txt，每行一个账号 | 无 |
| `--tag` | 输出文件名前缀 | 无 |
| `--gpu` | `auto` / `nvenc` / `qsv` / `amf` / `cpu` | `auto` |
| `--mirror` | `on` / `off` / `auto` | `off` |
| `--subs` | `keep`（保留） / `cover`（模糊盖） | `keep` |
| `--subs-area` | cover 遮盖底部比例 | 0.12 |
| `--jobs` | 并发数，0=自动 | GPU=4 / CPU=2 |
| `--seed` | 随机种子 | 随机 |
| `--interactive` | 交互式向导 | 否 |

## 强度档位对比

| 档位 | zoom | crop | 镜像 | 变速 | 变调 | 噪点 | 适用 |
|---|---|---|---|---|---|---|---|
| light | 1.00~1.02 | 0.975~0.995 | 关 | 1.0 | 1.0 | 无 | 高质量素材、少改画面 |
| standard | 1.02~1.045 | 0.95~0.98 | 50% | ±1.2% | ±0.6% | 轻 | 日常量产（推荐） |
| strong | 1.04~1.08 | 0.93~0.97 | 65% | ±2.2% | ±1.2% | 中 | 被判定过相似时用 |

## BGM 与账号表用法

**BGM 文件夹**：把 `.mp3/.m4a/.aac/.wav/.flac/.ogg` 放进 `bgm/`，工具会按顺序循环使用。

**账号分配表**：

```text
accounts/账号列表示例.txt
------------------------
drama_fans_01
drama_fans_02
reel_addict_01
```

运行后 `output/_账号分配表_*.csv` 会按轮次给每个账号分素材，保证同母版素材不会落到两个账号。

## 实测性能

环境：RTX 5060 Ti + NVENC，1080×1920 / 15 秒素材

| 模式 | 单条耗时 | 备注 |
|---|---|---|
| CPU (libx264) | ~6.1 秒/条 | 2 路并发 |
| GPU (NVENC) | ~3.5 秒/条 | 4 路并发 |

> 工具内部自动判断可用硬件编码；若 NVENC 因驱动/显存失败，会自动回退 CPU 重跑该条。

## 输出文件

- `xxx_v01.mp4`：去重后的素材
- `_去重记录_YYYYMMDD_HHMMSS.csv`：每版的全部随机参数
- `_账号分配表_YYYYMMDD_HHMMSS.csv`：账号↔素材映射

## FAQ

**Q1: 为什么默认关闭镜像？**  
A: 原片硬字幕镜像后会左右颠倒，严重影响观看。靠「非对称裁切」同样能拉开差异。

**Q2: BGM 还是大怎么办？**  
A: 用 `--bgm-vol 0.04` 继续调小。建议范围 0.05~0.12。

**Q3: 为什么画面上看还是有字幕？**  
A: `keep` 模式保留原片字幕；`cover` 模式只模糊底部固定比例区域，如果字幕不居中或过高，可调 `--subs-area`。

**Q4: 输出帧率变了？**  
A: 这是正常的。变速靠改变输出帧率实现，帧率会围绕原 fps 上下浮动 0.5~2.2%，但音视频严格同步。

**Q5: 双击 bat 没反应 / 一闪而过？**  
A: 常见原因有三个，按顺序排查：

1. **bat 文件被存成了 LF 换行**（从 GitHub 下载 zip、或用某些编辑器改过）。  
   cmd.exe 解析不了 LF-only 的多行括号块，会直接退出。  
   解决：用记事本打开 bat → 另存为 → 换行格式选 `Windows (CRLF)`；或在仓库里保留 `.gitattributes` 的 `*.bat -text` 规则。
2. **没装 Python，或装的是微软商店的占位版**。  
   启动器会逐个「真跑一次」验证候选解释器，占位程序会被跳过；  
   若全部不可用，会给出下载链接，并允许你直接把 `python.exe` 拖进窗口。
3. **没有 ffmpeg**。启动器会尝试自动 `pip install imageio-ffmpeg`；若失败会给出三种安装方式并停住。

如果窗口确实来不及看清，打开 CMD 手动跑一次就能看到完整报错：

```bat
cd /d "工具目录"
一键去重.bat
```

## 局限

- 工具只做「特征层去重」，不能替代剪辑层的叙事重构、AI 解说、字幕重写——这些才是 Meta 判断「原创性」的核心。
- 不同平台/账号的指纹策略不同，建议小批量测试后再放量。
- 本工具生成的素材请遵守目标平台社区准则与版权法规。

## 许可证

MIT License
