#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB 短剧素材批量去重工具 v2.1
============================================================
原理：Meta 判重看四层 —— 画面特征 / 音频特征 / 结构特征 / 文件特征。
      只改一层必被二次识别，本工具对四层同时做随机化变异，
      每个版本参数都不相同，输出全新编码的文件。

v2.1 修复：
  * 音画不同步 —— 改用「输出帧率」做变速，视频/音频时间因子严格一致，
                  两条流都在滤镜末尾重置时间戳（从 0 开始）。
  * BGM 音量过高 —— 默认 0.08（原 0.22），新增 --bgm-vol 可调，
                  amix 关闭自动归一化 + 末端限幅，原声不再被压小。
v2.0 特性：
  * GPU 硬件编码（自动检测 NVENC / QSV / AMF，失败自动回退 CPU）
  * --mirror 默认 off，避免原片硬字幕被左右镜像
  * --subs cover 用模糊条盖掉原片底部字幕带

用法：
  python dedupe.py -i input/ -n 5                       # 批量，每个视频出 5 版
  python dedupe.py -i a.mp4 -n 3 --mode strong          # 单个文件，强力模式
  python dedupe.py -i input/ -n 5 --bgm bgm/            # 叠加 BGM
  python dedupe.py -i input/ -n 5 --bgm bgm/ --bgm-vol 0.05
  python dedupe.py -i input/ -n 5 --subs cover          # 盖掉原片字幕
  python dedupe.py -i input/ -n 5 --accounts accounts/list.txt
  python dedupe.py --interactive                        # 交互式（双击 bat 用）
"""

from __future__ import annotations  # 类型注解在 Python 3.9 上也能正常求值

import argparse
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".ts", ".wmv", ".mpg", ".mpeg"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}

# CPU CRF -> GPU CQ 对标（实测：NVENC cq30 画质≈ libx264 crf23）
CRF_TO_CQ = {18: 22, 19: 23, 20: 24, 21: 26, 22: 28, 23: 30, 24: 32, 25: 34, 26: 36, 27: 38}

HERE = Path(__file__).resolve().parent
_LOCK = threading.Lock()


# ---------------------------------------------------------------- ffmpeg 定位
def find_ffmpeg() -> str:
    """查找顺序：工具自带 bin/ → 系统 PATH → 环境变量 FFMPEG_BIN → 常见安装位置
    → imageio-ffmpeg（pip 装的那个）。找不到返回空字符串。"""
    for cand in (HERE / "bin" / "ffmpeg.exe", HERE / "bin" / "ffmpeg"):
        if cand.exists():
            return str(cand)
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    env = os.environ
    home = Path(env.get("USERPROFILE", "") or str(Path.home()))
    lapp = Path(env.get("LOCALAPPDATA", "") or home / "AppData" / "Local")
    for cand in (
        env.get("FFMPEG_BIN", ""),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        str(lapp / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe"),
        str(home / "scoop" / "shims" / "ffmpeg.exe"),
        str(lapp / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"),
    ):
        if cand and Path(cand).exists():
            return str(Path(cand))
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


FFMPEG = find_ffmpeg()


def ensure_dirs():
    """确保工作目录存在（clone 后首次运行自动补齐）。"""
    for d in ("input", "output", "bgm", "accounts", "bin"):
        try:
            (HERE / d).mkdir(exist_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------- GPU 检测
def detect_gpu() -> str:
    """返回可用的硬件编码器：nvenc / qsv / amf / cpu。"""
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        enc = r.stdout or ""
    except Exception:
        return "cpu"
    # -encoders 里 NVENC/AMF 的标记是 "V....D"，QSV 是 "V....."，
    # 所以不能只匹配点号，V 开头即可。
    for name, tag in (("h264_nvenc", "nvenc"), ("h264_qsv", "qsv"), ("h264_amf", "amf")):
        if re.search(rf"^\s*V\S*\s+{re.escape(name)}\b", enc, re.M):
            return tag
    return "cpu"


def gpu_available(tag: str) -> bool:
    """真跑一次 1 秒编码，确认硬件编码可用（驱动/显存异常时回退）。"""
    if tag == "cpu":
        return True
    try:
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        if tag == "nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "35"]
        elif tag == "qsv":
            cmd += ["-c:v", "h264_qsv", "-global_quality", "35"]
        else:
            cmd += ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "35", "-qp_p", "35"]
        cmd += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "1", "-f", "null", "-"]
        return subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 媒体探测
def probe(src: Path) -> dict:
    """用 ffmpeg -i 解析时长/分辨率/帧率/是否含音轨（不依赖 ffprobe）。"""
    r = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    info = r.stderr
    dur, w, h, fps = 0.0, 0, 0, 30.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", info)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", info)
    if m:
        try:
            f = float(m.group(1))
            if 5.0 <= f <= 120.0:
                fps = f
        except ValueError:
            pass
    return {"duration": dur, "w": w, "h": h, "fps": fps, "has_audio": "Audio:" in info}


# ---------------------------------------------------------------- 参数生成
def make_params(rng: random.Random, mode: str, mirror_opt: str) -> dict:
    """按强度档位随机生成本版本的全套变异参数。"""
    if mode == "light":
        p = {
            "zoom": rng.uniform(1.00, 1.02),
            "crop_x": rng.uniform(0.975, 0.995),
            "crop_y": rng.uniform(0.975, 0.995),
            "off_x": rng.uniform(0.0, 1.0),
            "off_y": rng.uniform(0.0, 1.0),
            "mirror": False,
            "rot": 0.0,
            "bright": rng.uniform(-0.02, 0.02),
            "contrast": rng.uniform(0.98, 1.04),
            "satur": rng.uniform(0.97, 1.06),
            "hue": rng.uniform(-2.0, 2.0),
            "noise": 0,
            "sharp": rng.uniform(-0.3, 0.4),
            "speed": 1.0,
            "pitch": 1.0,
            "vol": 1.0,
            "ss": rng.uniform(0.0, 0.10),
            "crf": rng.randint(20, 23),
            "gop": rng.randint(48, 90),
            "abitrate": rng.choice(["160k", "192k"]),
        }
    elif mode == "strong":
        p = {
            "zoom": rng.uniform(1.04, 1.08),
            "crop_x": rng.uniform(0.93, 0.965),
            "crop_y": rng.uniform(0.928, 0.968),
            "off_x": rng.uniform(0.0, 1.0),
            "off_y": rng.uniform(0.0, 1.0),
            "mirror": rng.random() < 0.65,
            "rot": rng.uniform(-0.8, 0.8),
            "bright": rng.uniform(-0.045, 0.045),
            "contrast": rng.uniform(0.93, 1.08),
            "satur": rng.uniform(0.93, 1.12),
            "hue": rng.uniform(-4.0, 4.0),
            "noise": rng.randint(4, 8),
            "sharp": rng.uniform(-0.6, 0.8),
            "speed": rng.uniform(0.978, 1.022),
            "pitch": rng.uniform(0.988, 1.012),
            "vol": rng.uniform(0.94, 1.04),
            "ss": rng.uniform(0.0, 0.30),
            "crf": rng.randint(22, 25),
            "gop": rng.randint(30, 72),
            "abitrate": rng.choice(["128k", "160k", "192k"]),
        }
    else:  # standard
        p = {
            "zoom": rng.uniform(1.02, 1.045),
            "crop_x": rng.uniform(0.955, 0.982),
            "crop_y": rng.uniform(0.952, 0.980),
            "off_x": rng.uniform(0.0, 1.0),
            "off_y": rng.uniform(0.0, 1.0),
            "mirror": rng.random() < 0.5,
            "rot": rng.uniform(-0.45, 0.45),
            "bright": rng.uniform(-0.032, 0.032),
            "contrast": rng.uniform(0.955, 1.06),
            "satur": rng.uniform(0.95, 1.09),
            "hue": rng.uniform(-3.0, 3.0),
            "noise": rng.randint(2, 5),
            "sharp": rng.uniform(-0.45, 0.6),
            "speed": rng.uniform(0.988, 1.012),
            "pitch": rng.uniform(0.994, 1.006),
            "vol": rng.uniform(0.97, 1.03),
            "ss": rng.uniform(0.0, 0.20),
            "crf": rng.randint(21, 24),
            "gop": rng.randint(36, 84),
            "abitrate": rng.choice(["128k", "160k", "192k"]),
        }

    # ---- 镜像开关（默认关，保护原片硬字幕）
    if mirror_opt == "off":
        p["mirror"] = False
    elif mirror_opt == "on":
        p["mirror"] = mode != "light" and (p["mirror"] or rng.random() < 0.5)
    # auto：保持各档位自己的随机结果

    # ---- 关镜像时必须靠「非中心裁切」拉开差异，否则多版构图太像
    if not p["mirror"]:
        p["off_x"] = rng.uniform(0.12, 0.88)
        p["off_y"] = rng.uniform(0.12, 0.88)
    return p


# ---------------------------------------------------------------- 滤镜构建
def build_filters(p: dict, meta: dict, subs: str, subs_area: float) -> tuple:
    """返回 (video_filter_chain, audio_filter_chain)。

    时间轴设计（v2.1 关键修复）：
      视频：滤镜内不做任何变速，末尾 setpts=N/FRAME_RATE/TB 归一化时间戳；
            变速交给输出帧率 -r = fps * speed（帧数不变 → 时长 = 原时长 / speed）。
      音频：pitch 用 asetrate 变调后立刻 atempo=1/pitch 抵消时长变化；
            再 atempo=speed 与视频同步变速；末尾 asetpts=N/SR/TB 从 0 开始。
      两边时长同为 原时长/speed、起点同为 0，不再累积漂移。
    """
    ev = lambda v: int(v) & ~1
    W, H = ev(meta["w"]), ev(meta["h"])

    # ---- 画面层
    z = p["zoom"]
    cw, ch = ev(W * p["crop_x"]), ev(H * p["crop_y"])
    zw, zh = ev(W * z), ev(H * z)
    cw = min(cw, zw - 2)
    ch = min(ch, zh - 2)
    max_x, max_y = max(zw - cw, 0), max(zh - ch, 0)
    x, y = ev(max_x * p["off_x"]), ev(max_y * p["off_y"])

    # ---- 起点偏移：量化到帧边界后再裁
    # 视频是离散帧（25fps 一格 40ms），音频是连续采样。起点若不量化，
    # 音频能精确落到 0.035s 而视频只能落到 0.04s —— 一帧的差异就是可感知的滞后。
    fps = meta.get("fps") or 30.0
    ss_q = round(p["ss"] * fps) / fps
    p["ss_q"] = ss_q

    vf = []
    if ss_q > 0.001:
        vf.append(f"trim=start={ss_q:.6f}")
    vf += [f"scale={zw}:{zh}:flags=bicubic", f"crop={cw}:{ch}:{x}:{y}"]
    if abs(p["rot"]) > 0.02:
        rw, rh = ev(cw * 1.03), ev(ch * 1.03)
        vf.append(f"scale={rw}:{rh}:flags=bicubic")
        vf.append(f"rotate={p['rot']}*PI/180:c=black")
        vf.append(f"crop={cw}:{ch}")
    if p["mirror"]:
        vf.append("hflip")
    vf.append(f"scale={W}:{H}:flags=bicubic")
    vf.append(
        f"eq=brightness={p['bright']:.4f}:contrast={p['contrast']:.4f}"
        f":saturation={p['satur']:.4f}"
    )
    if abs(p["hue"]) > 0.05:
        vf.append(f"hue=h={p['hue']:.3f}")
    if p["noise"] > 0:
        vf.append(f"noise=alls={p['noise']}:allf=t")
    if abs(p["sharp"]) > 0.05:
        vf.append(f"unsharp=5:5:{p['sharp']:.2f}:5:5:0.0")
    vf.append("setsar=1")              # 重置像素宽高比，避免裁切后 SAR/DAR 畸变
    # VFR（可变帧率，平台下载的短剧很常见）会让时间戳抖动 -> 音画越走越偏。
    # fps 滤镜按真实时间轴重采样成 CFR，再归一化时间戳，双保险。
    vf.append(f"fps=fps={meta.get('fps') or 30:.6f}")
    vf.append("setpts=N/FRAME_RATE/TB")
    vf.append("format=yuv420p")
    v = ",".join(vf)

    # ---- 音频层（变调但不改时长 → 再与视频同步变速）
    a = ""
    if meta["has_audio"]:
        P, S, V = p["pitch"], p["speed"], p["vol"]
        sr = 48000
        af = [f"aresample={sr}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if ss_q > 0.001:
            af.append(f"atrim=start={ss_q:.6f}")
        if abs(P - 1.0) > 0.0005:
            # asetrate 会同时改变音高和时长，紧接着 atempo=1/P 把时长还原
            af.append(f"asetrate={int(round(sr * P))}")
            af.append(f"aresample={sr}")
            af.append(f"atempo={1.0 / P:.6f}")
        if abs(S - 1.0) > 0.0005:
            af.append(f"atempo={S:.6f}")
        af.append(f"volume={V:.4f}")
        af.append("asetpts=N/SR/TB")   # 音频时间戳从 0 开始，杜绝起始偏移
        a = ",".join(af)
    return v, a


def build_cmd(src: Path, out: Path, p: dict, meta: dict, bgm: Path | None,
              gpu: str, subs: str, subs_area: float, bgm_vol: float) -> list:
    v, a = build_filters(p, meta, subs, subs_area)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    # 注意：起点偏移必须放在「输出侧」。放输入侧时视频按关键帧 seek、
    # 音频按 AAC 帧边界 seek，两条流落点不同 -> 整条素材固定滞后几十毫秒。
    cmd += ["-i", str(src)]

    # ---- 字幕处理：把底部字幕带整条高斯模糊掉（原片硬字幕）
    def with_subs(chain_in: str) -> tuple:
        if subs != "cover":
            return f"[0:v]{chain_in}[v]", "[v]", None
        top = 1.0 - subs_area
        return (
            f"[0:v]{chain_in}[main];"
            f"[main]split=2[m1][m2];"
            f"[m2]crop=iw:ih*{subs_area:.4f}:0:ih*{top:.4f},gblur=sigma=28[bar];"
            f"[m1][bar]overlay=0:H*{top:.4f}[v]",
            "[v]", None,
        )

    vpart, vmap, _ = with_subs(v)

    if bgm and meta["has_audio"]:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        # BGM 单独重置时间戳；normalize=0 关闭自动归一化（否则原声被压掉一半）；
        # 末端 alimiter 兜底防削波
        fc = (
            f"{vpart};"
            f"[0:a]{a}[a0];"
            f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={bgm_vol:.4f},asetpts=N/SR/TB[bg];"
            f"[a0][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am];"
            f"[am]alimiter=limit=0.95:level=false[a]"
        )
        cmd += ["-filter_complex", fc, "-map", vmap, "-map", "[a]"]
    elif meta["has_audio"]:
        cmd += ["-filter_complex", f"{vpart};[0:a]{a}[a]", "-map", vmap, "-map", "[a]"]
    else:
        cmd += ["-filter_complex", vpart, "-map", vmap, "-an"]

    # ---- 输出帧率 = 源帧率 × 变速倍率（v2.1：变速改由帧率实现，音画必然同步）
    out_fps = meta["fps"] * p["speed"]
    cmd += ["-r", f"{out_fps:.6f}", "-fps_mode", "cfr"]

    # ---- 编码层
    cq = CRF_TO_CQ.get(p["crf"], 30)
    if gpu == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", str(cq), "-b:v", "0", "-tune", "hq",
                "-profile:v", "high", "-level", "4.1"]
    elif gpu == "qsv":
        cmd += ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(cq)]
    elif gpu == "amf":
        cmd += ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", str(cq), "-qp_p", str(cq)]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(p["crf"]),
                "-profile:v", "high", "-level", "4.1"]
    cmd += ["-pix_fmt", "yuv420p", "-g", str(p["gop"]),
            "-keyint_min", str(max(1, p["gop"] // 2))]

    if meta["has_audio"]:
        cmd += ["-c:a", "aac", "-b:a", p["abitrate"], "-ar", "48000", "-ac", "2"]

    # ---- 文件层：元数据清空 + 全新编码输出
    dur_out = max((meta["duration"] - p.get("ss_q", p["ss"])) / p["speed"] - 0.02, 0.5)
    cmd += [
        "-map_metadata", "-1",
        "-metadata", "encoder=",
        "-metadata", "title=",
        "-metadata", "comment=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:v:0", "handler_name=",
        "-metadata:s:a:0", "encoder=",
        "-metadata:s:a:0", "handler_name=",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-movflags", "+faststart",
        "-t", f"{dur_out:.3f}",
        "-shortest",
        str(out),
    ]
    return cmd


# ---------------------------------------------------------------- 单任务
def render_one(src: Path, out_dir: Path, idx: int, mode: str, seed: int,
               bgm: Path | None, meta: dict, tag: str, log: list, gpu: str,
               mirror: str, subs: str, subs_area: float, bgm_vol: float) -> dict:
    rng = random.Random(seed)
    p = make_params(rng, mode, mirror)
    base = f"{tag}_{src.stem}_v{idx:02d}" if tag else f"{src.stem}_v{idx:02d}"
    out = out_dir / f"{base}.mp4"
    n = 1
    while out.exists():
        n += 1
        out = out_dir / f"{base}_{n}.mp4"

    cmd = build_cmd(src, out, p, meta, bgm, gpu, subs, subs_area, bgm_vol)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    cost = time.time() - t0
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 4096

    used_gpu = gpu
    if not ok and gpu != "cpu":
        # 硬件编码失败（驱动/显存/并发上限）自动回退 CPU 重跑一次
        cmd2 = build_cmd(src, out, p, meta, bgm, "cpu", subs, subs_area, bgm_vol)
        r2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        cost += time.time() - t0
        ok = r2.returncode == 0 and out.exists() and out.stat().st_size > 4096
        used_gpu = "cpu" if ok else gpu
        r = r2

    rec = {
        "源文件": src.name,
        "版本": f"v{idx:02d}",
        "输出文件": out.name,
        "状态": "OK" if ok else "失败",
        "耗时秒": f"{cost:.1f}",
        "编码器": used_gpu,
        "质量档位": f"cq{CRF_TO_CQ.get(p['crf'], 30)}" if used_gpu != "cpu" else f"crf{p['crf']}",
        "放大倍数": f"{p['zoom']:.4f}",
        "裁切比X": f"{p['crop_x']:.4f}",
        "裁切比Y": f"{p['crop_y']:.4f}",
        "裁切偏移X": f"{p['off_x']:.3f}",
        "裁切偏移Y": f"{p['off_y']:.3f}",
        "镜像": "是" if p["mirror"] else "否",
        "旋转度": f"{p['rot']:.2f}",
        "亮度": f"{p['bright']:.4f}",
        "对比度": f"{p['contrast']:.4f}",
        "饱和度": f"{p['satur']:.4f}",
        "色相": f"{p['hue']:.2f}",
        "噪点": p["noise"],
        "锐化": f"{p['sharp']:.2f}",
        "变速倍率": f"{p['speed']:.5f}",
        "输出帧率": f"{meta['fps'] * p['speed']:.4f}",
        "变调倍率": f"{p['pitch']:.5f}",
        "音量": f"{p['vol']:.4f}",
        "起点偏移秒": f"{p['ss']:.3f}",
        "字幕处理": subs,
        "BGM": bgm.name if bgm else "",
        "BGM音量": f"{bgm_vol:.3f}" if bgm else "",
        "GOP": p["gop"],
        "音频码率": p["abitrate"],
        "错误": (r.stderr or "").strip().replace("\n", " ")[:200] if not ok else "",
    }
    with _LOCK:
        log.append(rec)
        flag = "OK " if ok else "FAIL"
        print(f"  [{flag}] {out.name}" + ("" if ok else f"  <- {rec['错误'][:90]}"), flush=True)
    return rec


# ---------------------------------------------------------------- 主流程
def collect(path: Path) -> list:
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXT else []
    return sorted(
        [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT]
    )


def run(jobs_cfg: dict) -> int:
    srcs = collect(jobs_cfg["input"])
    if not srcs:
        print(f"!! 没找到视频文件：{jobs_cfg['input']}")
        return 1

    out_dir: Path = jobs_cfg["output"]
    out_dir.mkdir(parents=True, exist_ok=True)
    bgms = []
    if jobs_cfg["bgm"]:
        bgms = sorted([f for f in jobs_cfg["bgm"].rglob("*")
                       if f.is_file() and f.suffix.lower() in AUDIO_EXT])

    accounts = []
    if jobs_cfg["accounts"]:
        accounts = [x.strip() for x in jobs_cfg["accounts"].read_text(
            encoding="utf-8", errors="ignore").splitlines() if x.strip()]

    gpu = jobs_cfg["gpu"]
    if gpu == "auto":
        gpu = detect_gpu()
        if gpu != "cpu" and not gpu_available(gpu):
            gpu = "cpu"

    print("=" * 62)
    print("  FB 短剧素材批量去重工具 v2.1")
    print("=" * 62)
    print(f"  输入      : {jobs_cfg['input']}")
    print(f"  视频数量  : {len(srcs)}")
    print(f"  每视频版本: {jobs_cfg['versions']}")
    print(f"  强度档位  : {jobs_cfg['mode']}")
    print(f"  编码加速  : {'GPU ' + gpu.upper() if gpu != 'cpu' else 'CPU（未检测到可用硬件编码）'}")
    print(f"  镜像      : {'开' if jobs_cfg['mirror'] != 'off' else '关（保护原片字幕）'}")
    print(f"  字幕处理  : {jobs_cfg['subs']}" +
          (f"（底部 {jobs_cfg['subs_area'] * 100:.0f}%）" if jobs_cfg["subs"] == "cover" else ""))
    print(f"  BGM       : {len(bgms)} 个" if bgms else "  BGM       : 未启用")
    if bgms:
        print(f"  BGM 音量  : {jobs_cfg['bgm_vol']:.2f}")
    print(f"  账号列表  : {len(accounts)} 个" if accounts else "  账号列表  : 未提供")
    print(f"  并发任务  : {jobs_cfg['jobs']}")
    print(f"  输出目录  : {out_dir}")
    print("=" * 62, flush=True)

    total = len(srcs) * jobs_cfg["versions"]
    print(f"  预计输出 {total} 条素材，开始处理...\n", flush=True)

    log, mapping, tasks = [], [], []
    si = 0
    for s in srcs:
        meta = probe(s)
        if not meta["w"] or not meta["h"]:
            print(f"  [SKIP] {s.name} 无法读取分辨率，跳过", flush=True)
            continue
        for i in range(1, jobs_cfg["versions"] + 1):
            si += 1
            seed = jobs_cfg["seed"] * 100003 + si
            bgm = bgms[(si - 1) % len(bgms)] if bgms else None
            tasks.append((s, meta, i, seed, bgm))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=jobs_cfg["jobs"]) as pool:
        futs = [
            pool.submit(render_one, s, out_dir, i, jobs_cfg["mode"], sd, bg,
                        mt, jobs_cfg["tag"], log, gpu, jobs_cfg["mirror"],
                        jobs_cfg["subs"], jobs_cfg["subs_area"], jobs_cfg["bgm_vol"])
            for (s, mt, i, sd, bg) in tasks
        ]
        for _ in as_completed(futs):
            pass

    cost = time.time() - t0
    ok_n = sum(1 for r in log if r["状态"] == "OK")

    if accounts:
        ok_files = [r["输出文件"] for r in log if r["状态"] == "OK"]
        for n, f in enumerate(ok_files):
            mapping.append({"账号": accounts[n % len(accounts)],
                            "分配素材": f, "轮次": n // len(accounts) + 1})

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lp = out_dir / f"_去重记录_{stamp}.csv"
    if log:
        with open(lp, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=list(log[0].keys()))
            wr.writeheader()
            wr.writerows(log)
    mp = None
    if mapping:
        mp = out_dir / f"_账号分配表_{stamp}.csv"
        with open(mp, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=["账号", "分配素材", "轮次"])
            wr.writeheader()
            wr.writerows(mapping)

    print("\n" + "=" * 62)
    print(f"  完成：成功 {ok_n} / {len(log)} 条，耗时 {cost:.1f} 秒"
          + (f"（{cost / max(ok_n,1):.2f} 秒/条）" if ok_n else ""))
    print(f"  输出目录: {out_dir}")
    print(f"  去重记录: {lp.name}")
    if mp:
        print(f"  账号分配: {mp.name}")
    if ok_n < len(log):
        print(f"  !! 有 {len(log) - ok_n} 条失败，详见记录表最后一列")
    print("=" * 62)
    return 0


# ---------------------------------------------------------------- 交互模式
def ask(msg, default=""):
    """读取一行输入；输入流被关闭/用户中断时，当作直接回车（用默认值）。"""
    try:
        v = input(msg).strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print()
        v = ""
    return v if v else default


def interactive(prefill: str = ""):
    print("=" * 62)
    print("  FB 短剧素材批量去重工具 v2.1  (直接回车 = 用默认值)")
    print("=" * 62)
    print()
    if prefill:
        print(f"  已接收拖入路径：{prefill}")
        inp = prefill
    else:
        inp = ask("1) 把视频/文件夹拖到这里然后回车：")
    if not inp:
        print("!! 没有输入路径，退出")
        return 1
    ip = Path(inp)
    if not ip.exists():
        print(f"!! 路径不存在：{ip}")
        return 1

    n = ask("2) 每个视频生成几个版本？[默认 5]：", "5")
    try:
        n = max(1, min(50, int(n)))
    except ValueError:
        n = 5

    print("3) 去重强度：")
    print("     [1] light    轻度 —— 只改画面裁切/调色/编码（画质最好）")
    print("     [2] standard 标准 —— 画面+音调+变速+镜像（推荐）")
    print("     [3] strong   强力 —— 上面全做，幅度更大 + 建议配 BGM")
    m = ask("   选择 [默认 2]：", "2")
    mode = {"1": "light", "2": "standard", "3": "strong"}.get(m, "standard")

    print("4) 原片字幕处理：")
    print("     [1] keep   保留（推荐，镜像已默认关闭不会镜像字幕）")
    print("     [2] cover  模糊盖掉底部字幕带（要重写字幕时用）")
    s = ask("   选择 [默认 1]：", "1")
    subs = {"1": "keep", "2": "cover"}.get(s, "keep")

    out = HERE / "output"
    o = ask(f"5) 输出目录 [默认 {out}]：", str(out))

    bgm_dir = None
    b = ask("6) 是否叠加 BGM？（把文件夹拖进来，不叠加直接回车）：")
    bgm_vol = 0.08
    if b and Path(b).exists():
        bgm_dir = Path(b)
        bv = ask("   BGM 音量 0.01~0.30 [默认 0.08，越小越不抢人声]：", "0.08")
        try:
            bgm_vol = max(0.01, min(0.30, float(bv)))
        except ValueError:
            bgm_vol = 0.08

    acc = None
    a = ask("7) 账号列表 txt 路径（可选，用于生成分配表，直接回车跳过）：")
    if a:
        p = Path(a) if len(a) > 3 and ("\\" in a or "/" in a) else HERE / "accounts" / a
        if not p.suffix:
            p = p.with_suffix(".txt")
        if p.exists():
            acc = p
        else:
            print(f"   (没找到 {p}，跳过分配表)")

    g = ask("8) 编码方式 [1]=自动(优先GPU) [2]=强制CPU  [默认 1]：", "1")
    gpu = "cpu" if g == "2" else "auto"

    j_default = "4" if gpu == "auto" else "2"
    j = ask(f"9) 同时处理任务数 [默认 {j_default}，电脑卡就调小]：", j_default)
    try:
        j = max(1, min(8, int(j)))
    except ValueError:
        j = int(j_default)

    print()
    print("=" * 62)
    print("  输入   :", ip)
    print("  版本数 :", n)
    print("  强度   :", mode)
    print("  字幕   :", subs)
    print("  输出   :", o)
    print("  BGM    :", f"{bgm_dir}（音量 {bgm_vol}）" if bgm_dir else "不使用")
    print("  账号表 :", acc if acc else "不使用")
    print("  编码   :", "自动检测（优先 GPU）" if gpu == "auto" else "CPU")
    print("  并发   :", j)
    print("=" * 62)
    if ask("确认开始？(y/n) [默认 y]：", "y").lower() not in ("y", "yes", "是", ""):
        print("已取消。")
        return 0

    return run({
        "input": ip, "output": Path(o), "versions": n, "mode": mode,
        "bgm": bgm_dir, "accounts": acc, "jobs": j,
        "tag": "", "seed": random.randint(1, 10 ** 6),
        "gpu": gpu, "mirror": "off", "subs": subs, "subs_area": 0.12,
        "bgm_vol": bgm_vol,
    })


# ---------------------------------------------------------------- CLI
def main():
    ensure_dirs()
    if not FFMPEG:
        print("!! 没找到 ffmpeg，程序无法运行。")
        print()
        print("   任选一种方式装好 ffmpeg：")
        print("     1) pip install imageio-ffmpeg          （最简单，推荐）")
        print("     2) 下载 ffmpeg.exe 放到本目录的 bin 文件夹里")
        print("        https://www.gyan.dev/ffmpeg/builds/")
        print("     3) 装到系统 PATH，或设置环境变量 FFMPEG_BIN 指向 ffmpeg.exe")
        return 1

    ap = argparse.ArgumentParser(description="FB 短剧素材批量去重工具 v2.1")
    ap.add_argument("-i", "--input", help="视频文件或文件夹")
    ap.add_argument("-o", "--output", default=str(HERE / "output"), help="输出目录")
    ap.add_argument("-n", "--versions", type=int, default=5, help="每个视频生成几个版本")
    ap.add_argument("--mode", choices=["light", "standard", "strong"], default="standard")
    ap.add_argument("--bgm", help="BGM 文件夹")
    ap.add_argument("--bgm-vol", type=float, default=0.08,
                    help="BGM 音量 0.01~0.30，默认 0.08（越低越不抢人声）")
    ap.add_argument("--accounts", help="账号列表 txt")
    ap.add_argument("--tag", default="", help="输出文件名前缀（如账号或剧名）")
    ap.add_argument("--gpu", choices=["auto", "nvenc", "qsv", "amf", "cpu"], default="auto",
                    help="编码方式，默认 auto（自动检测硬件编码，失败回退 CPU）")
    ap.add_argument("--mirror", choices=["on", "off", "auto"], default="off",
                    help="左右镜像，默认 off（避免原片硬字幕被镜像）")
    ap.add_argument("--subs", choices=["keep", "cover"], default="keep",
                    help="原片字幕处理：keep 保留 / cover 模糊盖掉底部字幕带")
    ap.add_argument("--subs-area", type=float, default=0.12,
                    help="cover 模式遮盖的画面底部比例，默认 0.12")
    ap.add_argument("--jobs", type=int, default=0, help="并发任务数（0=自动，GPU 4 / CPU 2）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（同种子结果可复现）")
    ap.add_argument("--interactive", action="store_true", help="交互式向导")
    args = ap.parse_args()

    if args.interactive or not args.input:
        return interactive(args.input or "")

    gpu = args.gpu
    jobs = args.jobs
    if jobs <= 0:
        jobs = 2 if gpu == "cpu" else 4
    if gpu == "auto":
        gpu = detect_gpu()

    return run({
        "input": Path(args.input), "output": Path(args.output),
        "versions": max(1, min(50, args.versions)), "mode": args.mode,
        "bgm": Path(args.bgm) if args.bgm else None,
        "accounts": Path(args.accounts) if args.accounts else None,
        "jobs": max(1, min(8, jobs)), "tag": args.tag,
        "seed": args.seed if args.seed is not None else random.randint(1, 10 ** 6),
        "gpu": gpu, "mirror": args.mirror, "subs": args.subs,
        "subs_area": max(0.05, min(0.35, args.subs_area)),
        "bgm_vol": max(0.01, min(0.30, args.bgm_vol)),
    })


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
