# FB 短剧素材批量去重工具

> 一条母版裂变 N 条底层特征完全不同的素材。四层随机变异 + GPU 硬件编码加速 + 可控镜像/字幕处理 + 全参数留痕 + 账号分配表。
> 专为短剧出海矩阵（上百账号）设计的本地批处理工具，开箱即用，素材不出本机。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![FFmpeg](https://img.shields.io/badge/FFmpeg-7.1-green)
![GPU](https://img.shields.io/badge/GPU-NVENC%2FQSV%2FAMF-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 目录

- [这个工具解决什么问题](#这个工具解决什么问题)
- [工作原理](#工作原理)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [命令行用法](#命令行用法)
- [强度档位](#强度档位)
- [GPU 加速](#gpu-加速)
- [字幕与镜像](#字幕与镜像)
- [账号分配表](#账号分配表)
- [输出说明](#输出说明)
- [实测性能](#实测性能)
- [常见问题](#常见问题)
- [局限与注意事项](#局限与注意事项)
- [目录结构](#目录结构)
- [FFmpeg 与许可证](#ffmpeg-与许可证)
- [English](#english)

---

## 这个工具解决什么问题

做短剧出海矩阵的人基本都踩过这个坑：同一条素材改个边框、加个变速、换个低音量 BGM，发到不同账号上，一开始还能过，后来平台判重越来越准，直接不推流。

原因在于**平台的判重看的是内容指纹，不是文件外观**。只改一层（比如只加边框或只变速），其他层的特征原封不动，二次识别照样命中。

据 Meta 公开说明，其在 2026 年 3 月起收紧了对"原创内容"的判定，并把**加边框、加字幕、变速这类表面编辑**明确归为"未做实质转换"，同时收紧了对重复上传、纯片段拼接的分发。判定是**账号级**的：一个账号反复发低原创内容，会拖累它全部内容的分发。

这个工具做的事很具体：**把一条母版素材，在画面、音频、结构、文件四个特征层上同时做随机变异，一次产出多个互不相同的版本**，让同一母版的裂变版本不再互相撞车。

需要说清楚的是：**它只处理"特征层"，不解决"原创性"**。真正决定推流的是叙事重构、解说、字幕重写这些剪辑阶段的工作。工具是产线里的一环，不是替代品。

## 工作原理

Meta 一类的判重系统通常对四层做比对，所以工具对四层同时动手：

| 层级 | 比对什么 | 工具怎么做 |
|---|---|---|
| **画面层** | 关键帧哈希、色彩直方图、主体位置 | 放大 → 非对称随机裁切 → 回填原尺寸；微旋转；亮度/对比度/饱和度/色相随机；轻噪点；锐化。镜像默认关闭（原片有字幕时不会把字幕翻反），关闭时裁切会强制偏离中心保证差异 |
| **音频层** | 声纹、BGM 指纹、波形哈希 | 音高微变（±1.2% 以内，人耳无感）+ 速度补偿 + 音量微调 + 可选叠加 BGM |
| **结构层** | 片段顺序、时长分布、首帧 | 起点微偏移（改变时长与首帧）+ 随机变速（±2.2% 以内） |
| **文件层** | 元数据、编码参数、文件哈希 | 拍摄设备/GPS/时间全清、去版本指纹、随机 CRF/GOP/音频码率、全新编码输出（全新 MD5） |

**关键点：每个版本的每一项参数都是独立随机生成的**，不是固定参数套一遍。所以同一母版裂变出来的 v01 和 v02，在四个层上都不一样。所有参数会写进 CSV 记录表，方便追溯哪个账号用了哪套参数。

输出的硬性保证：分辨率与源一致（典型 1080×1920 / 9:16）、SAR 归正为 1:1（不会出现比例畸变）、无黑边、时长基本不变。

## 功能特性

- **四层同时变异**，不是单层微调
- **GPU 硬件编码加速**：自动检测 NVIDIA NVENC / Intel QSV / AMD AMF
- **镜像可控**：默认关闭，避免原片硬字幕左右颠倒
- **字幕处理**：保留原字幕，或用模糊条盖住底部字幕带（便于后续重写）
- **三档强度**：`light` / `standard` / `strong`，按素材状态选
- **全参数留痕**：每个版本的裁切比例、偏移、镜像、旋转、调色值、变速倍率、变调倍率、CRF、GOP 全部记录成 CSV
- **账号分配表**：给一份账号列表，自动生成"账号 → 素材"分配表，按轮次轮询分配
- **批量处理**：扫描整个文件夹（含子目录），多任务并发
- **BGM 轮换**：BGM 文件夹里放多条，自动轮流叠加到各版本
- **纯本地处理**：素材不经过任何服务器
- **零第三方依赖**：只用 Python 标准库 + ffmpeg 二进制
- **两种入口**：Windows 双击 bat 图形向导，或命令行批处理

## 快速开始

### 前置：拿到 ffmpeg

工具会按顺序自动查找 ffmpeg：`bin/ffmpeg.exe` → 系统 PATH → `imageio-ffmpeg` 包。任选一种方式准备：

**方式 A（推荐，最省事）**

```bash
pip install imageio-ffmpeg
```

**方式 B（放进工具目录，完全自包含）**

从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载 essentials build，解压后把 `ffmpeg.exe` 放到 `bin/` 目录下。

**方式 C**：系统已装过 ffmpeg 并在 PATH 里，直接用。

### 方式一：双击运行（Windows）

1. 双击 **`一键去重.bat`**
2. 把视频文件或整个文件夹**拖进窗口**，回车
3. 每个视频出几版（回车 = 5）
4. 选强度：`1` 轻度 / `2` 标准（推荐）/ `3` 强力
5. 原片有硬字幕？`1` 有字幕（镜像关）/ `2` 无字幕（可开镜像）
6. 字幕处理？`1` 保留 / `2` cover 模糊盖住底部字幕带
7. 输出目录（回车 = `output\`）
8. BGM（拖入文件夹或回车跳过）
9. 账号列表 txt（拖入或回车跳过）
10. GPU 加速（回车 = 自动检测）
11. 并发数（回车 = GPU 自动 4 / CPU 自动 2）
12. 输入 `y` 开始，跑完去 `output\` 取文件

也可以直接把视频或文件夹**拖到 `一键去重.bat` 图标上**。

### 方式二：命令行

```bash
# 自动检测 GPU，默认关镜像（保护字幕）
python dedupe.py -i input/ -n 5

# 强制用 NVIDIA GPU，原片无字幕可开镜像
python dedupe.py -i input/ -n 8 --gpu nvenc --mirror on

# 有硬字幕：关镜像 + 模糊盖字幕，之后自己重新加字幕
python dedupe.py -i input/ -n 8 --mirror off --subs cover --subs-area 0.12

# 强制 CPU、4 并发
python dedupe.py -i input/ -n 10 --gpu cpu --jobs 4 --bgm bgm/
```

## 命令行用法

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-i, --input` | 输入视频文件或文件夹（递归扫描） | 必填 |
| `-o, --output` | 输出目录 | `output/` |
| `-n, --versions` | 每个视频生成几个版本（1–50） | `5` |
| `--mode` | 强度档位：`light` / `standard` / `strong` | `standard` |
| `--gpu` | 编码加速：`auto` / `nvenc` / `qsv` / `amf` / `cpu` | `auto` |
| `--jobs` | 并发任务数（默认自动：GPU=4 / CPU=2） | 自动 |
| `--mirror` | 镜像开关：`on` / `off` | `off` |
| `--subs` | 硬字幕处理：`keep` / `cover` | `keep` |
| `--subs-area` | cover 模式字幕带高度占比（0.03–0.4） | `0.12` |
| `--bgm` | BGM 文件夹 | 不启用 |
| `--accounts` | 账号列表 txt | 不启用 |
| `--tag` | 输出文件名前缀 | 空 |
| `--seed` | 随机种子 | 随机 |
| `--interactive` | 交互式向导 | — |

支持的视频格式：`mp4 / mov / mkv / avi / webm / m4v / flv / ts / wmv / mpg` 等。

## 强度档位

| 档位 | 变异范围 | 画质影响 | 适用场景 |
|---|---|---|---|
| `light` | 裁切 2–4%、调色微调、无变速无镜像 | 极小 | 高价值素材、二压前最后一步 |
| `standard` | 裁切 4–5%、调色、噪点、变速 ±1.2%、变调 ±0.6% | 很小 | **日常量产（推荐）** |
| `strong` | 裁切 6–8%、旋转 ±0.8°、噪点更强、变速 ±2.2%、变调 ±1.2% | 轻微可见 | 已被标记过的素材、需要大幅拉开差异 |

强度不是越高越好。幅度越大画面越"松"，日常量产用 `standard` 就够。

## GPU 加速

工具启动时会自动实测一次硬件编码（不只查编码器列表），确保驱动真的可用。支持优先级：

1. **NVIDIA NVENC**（消费级 GeForce/RTX 都支持）
2. **Intel QSV**（带核显的 Intel CPU）
3. **AMD AMF**
4. fallback 到 **CPU libx264**

如果 GPU 编码失败（显存不足、驱动异常），单条任务会自动回退到 CPU 编码，不会中断。

质量档位映射（实测）：`libx264 -crf 23` ≈ `h264_nvenc -cq 30`。

## 字幕与镜像

短剧素材的字幕通常是**烧进画面里的硬字幕**。如果直接镜像，字幕会左右颠倒，这条素材就废了。

因此工具默认：

- `--mirror off`：不做镜像，字幕保持正常
- `--subs keep`：保留原字幕

关闭镜像时，工具会**自动把非对称裁切偏移强制到 0.12~0.88 区域**（远离中心），用构图差异弥补镜像差异。

如果你想按 Meta 新规"字幕全重写"，可以 `--subs cover`：

1. 用模糊条盖住底部原字幕带（高度默认 12%）
2. 在剪映/PR 里重新加自己的字幕
3. 新字幕不要放得太低，以免被盖住

## 账号分配表

把账号名一行一个写进 txt：

```
drama_fans_01
drama_fans_02
drama_fans_03
```

加 `--accounts` 参数运行后，输出目录会多出 `_账号分配表_日期.csv`：

| 账号 | 分配素材 | 轮次 |
|---|---|---|
| drama_fans_01 | xxx_v01.mp4 | 1 |
| drama_fans_02 | xxx_v02.mp4 | 1 |
| drama_fans_03 | xxx_v03.mp4 | 2 |

按顺序轮询分配，一轮分完自动进入下一轮，保证同一素材不会被两个账号重复使用。上百账号的矩阵照着表发布即可。

## 输出说明

```
output/
├── xxx_v01.mp4               # 第 1 版
├── xxx_v02.mp4               # 第 2 版（参数与 v01 完全不同）
├── ...
├── _去重记录_20260918_105048.csv    # 每个版本用了什么参数
└── _账号分配表_20260918_105048.csv  # 仅在提供 --accounts 时生成
```

去重记录表的列包括：源文件、版本、输出文件、状态、耗时、放大倍数、裁切比 X/Y、裁切偏移 X/Y、镜像、旋转度、亮度、对比度、饱和度、色相、噪点、锐化、变速倍率、变调倍率、音量、起点偏移秒、CRF、质量档位、GOP、音频码率、编码器、字幕处理、BGM、错误。

## 实测性能

测试环境：Windows 11 / RTX 5060 Ti / 1080×1920 / 15 秒素材 / 4 并发

| 模式 | 耗时/条 |
|---|---|
| CPU libx264 medium | 约 6.0 秒 |
| GPU NVENC p4 | 约 2.4 秒 |

**GPU 模式相比 CPU 提升约 2.5 倍**；素材越长、版本越多，GPU 优势越明显。

## 常见问题

**Q：双击 bat 提示找不到 Python？**
装 Python 3.8+（[python.org](https://www.python.org/downloads/)），安装时务必勾选 **Add python.exe to PATH**。

**Q：处理还是很慢？**
看开头输出的"编码加速"是否识别到了 GPU。如果显示"CPU libx264"，检查显卡驱动，或在交互模式里确认没选"只用 CPU"。

**Q：字幕反了 / 左右颠倒？**
原片有硬字幕时镜像必须关。命令行用 `--mirror off`，或在交互模式里选"有字幕"。

**Q：cover 模糊条把我新加的字幕也盖住了？**
cover 盖的是原片底部字幕带，默认 12%。你自己加字幕时别放得太低，或调小 `--subs-area`。

**Q：同一部剧的多条母版互相撞车？**
工具解决的是同一母版裂变版本之间的差异。不同母版之间的差异要在剪辑阶段拉开（不同开头钩子、不同镜头组合），同剧素材间重复画面控制在 30% 以内。

**Q：输出文件的 `Lavc libx264` 编码器标识去不掉？**
`-fflags +bitexact` 已经去掉了版本号，但 mp4 容器仍会保留 `Lavc` 这一结构性字段。它不含设备或来源信息，且几乎所有 ffmpeg 处理过的视频都有，不构成判重依据。真正重要的元数据（拍摄设备、GPS、创建时间）已全部清空。

**Q：素材会不会被上传到服务器？**
不会。全程本地 ffmpeg 处理，无任何网络请求。

## 局限与注意事项

**这个工具不做的（必须在剪辑阶段人工完成）**：

1. 叙事重构 —— 不按原片顺序剪，开头 3 秒换钩子
2. 解说轨道 —— 配 AI 或真人解说（这是满足平台"新增价值"判定最有效的一招）
3. 字幕重写 —— 不照抄原片台词
4. BGM 更换 —— 优先用平台官方免费音乐库

正确用法是：**人工按上述流程剪出母版 → 工具裂变 N 条 → 按分配表分发给各账号。**

**其他提醒**：

- 被降权的账号光靠发新素材救不回来，需要先持续发布高原创内容重置账号信号
- 请确认你拥有所处理素材的使用授权
- 本工具是技术工具，不保证任何平台的分发结果，平台规则持续变化

## 目录结构

```
.
├── 一键去重.bat          # Windows 双击启动
├── dedupe.py             # 核心引擎（CLI + 交互向导）
├── 使用说明.txt           # 中文详细说明
├── README.md             # 本文件
├── LICENSE               # MIT
├── .gitignore            # 忽略素材/产物/账号文件
├── .gitattributes        # 换行符规范
├── bin/                  # ffmpeg 放这里（可选）
├── input/                # 放待处理素材
├── output/               # 输出目录
├── bgm/                  # 放 BGM 文件
└── accounts/             # 放账号列表 txt
```

## FFmpeg 与许可证

- **本项目代码**：MIT License
- **FFmpeg**：本项目不捆绑 ffmpeg 二进制，需你自行获取（见[快速开始](#快速开始)）。FFmpeg 为 LGPL/GPL 许可，使用时请遵守其许可条款。

---

## English

**FB Short Drama Batch Deduplication Tool v2.0**

Takes one master clip and produces multiple versions that differ across **four fingerprint layers**: visual (asymmetric crop, rotation, color grading, noise), audio (pitch/speed/volume jitter, optional BGM overlay), structural (start offset, speed change), and file-level (metadata scrub, randomized encoding parameters, fresh encode).

**Key upgrades in v2.0**:
- **GPU hardware encoding** (NVIDIA NVENC / Intel QSV / AMD AMF), ~2.5× faster than CPU on RTX 5060 Ti
- **Mirror is off by default** to protect burned-in subtitles from being flipped
- **Subtitle cover mode** blurs the bottom subtitle strip so you can re-burn your own subtitles

**Quick start**

```bash
pip install imageio-ffmpeg      # or put ffmpeg.exe into bin/
python dedupe.py -i input/ -n 5 --gpu auto
```

On Windows, just double-click `一键去重.bat` and drag your folder into the window.

**What it does NOT do**: it handles fingerprint-level variation only. Narrative restructuring, commentary tracks, subtitle rewriting and BGM replacement must still be done during editing — those are what actually determine originality-related distribution. Use this tool as one step of your pipeline, not a replacement for it.

No files leave your machine. All processing is local.

---
