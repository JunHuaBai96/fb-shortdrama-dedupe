#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB 短剧素材批量去重工具 v1.0
============================================================
原理：Meta 判重看四层 —— 画面特征 / 音频特征 / 结构特征 / 文件特征。
      只改一层必被二次识别，本工具对四层同时做随机化变异，
      每个版本参数都不相同，输出全新编码的文件。

用法：
  python dedupe.py -i input/ -n 5                    # 批量，每个视频出 5 版
  python dedupe.py -i a.mp4 -n 3 --mode strong       # 单个文件，强力模式
  python dedupe.py -i input/ -n 5 --bgm bgm/         # 叠加 BGM
  python dedupe.py -i input/ -n 5 --accounts accounts/list.txt
  python dedupe.py --interactive                     # 交互式（双击 bat 用）
"""

import argparse
import csv
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

HERE = Path(__file__).resolve().parent
_LOCK = threading.Lock()


# ---------------------------------------------------------------- ffmpeg 定位
def find_ffmpeg() -> str:
    """优先用工具自带 bin/ffmpeg.exe，其次系统 PATH，最后 imageio-ffmpeg。"""
    for cand in (HERE / "bin" / "ffmpeg.exe", HERE / "bin" / "ffmpeg"):
        if cand.exists():
            return str(cand)
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


FFMPEG = find_ffmpeg()

# 硬件编码器优先级（按顺序实测探测，确保驱动真的可用）
GPU_ENCODERS = [("nvenc", "h264_nvenc"), ("qsv", "h264_qsv"), ("amf", "h264_amf")]


def detect_gpu(ffmpeg: str) -> tuple:
    """实测一次硬件编码，返回 (名字, 编码器)。不只看编码器列表，要看驱动能不能跑。"""
    if not ffmpeg:
        return "cpu", "libx264"
    for name, enc in GPU_ENCODERS:
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "testsrc2=size=640x360:rate=25:duration=1",
                 "-c:v", enc, "-f", "null", "-"],
                capture_output=True, timeout=90,
            )
            if r.returncode == 0:
                return name, enc
        except Exception:
            continue
    return "cpu", "libx264"


def ensure_dirs():
    """确保工作目录存在（clone 后首次运行自动补齐）。"""
    for d in ("input", "output", "bgm", "accounts", "bin"):
        try:
            (HERE / d).mkdir(exist_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------- 媒体探测
def probe(src: Path) -> dict:
    """用 ffmpeg -i 解析时长/分辨率/是否含音轨（不依赖 ffprobe）。"""
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
            fps = float(m.group(1))
        except ValueError:
            pass
    return {"duration": dur, "w": w, "h": h, "fps": fps, "has_audio": "Audio:" in info}


# ---------------------------------------------------------------- 参数生成
def make_params(rng: random.Random, mode: str) -> dict:
    """按强度档位随机生成本版本的全套变异参数。"""
    if mode == "light":
        # 只做画面裁切 + 调色 + 元数据 + 重编码（画质损失最小，适合高价值素材）
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
    return p


# ---------------------------------------------------------------- 滤镜构建
def build_filters(p: dict, meta: dict, mirror_on: bool = True) -> tuple:
    """返回 (video_filter_chain, audio_filter_chain)

    mirror_on=False 时彻底禁用镜像（原片有硬字幕时必须关，否则字幕左右颠倒）。
    作为补偿，裁切偏移会强制偏离画面中心，保证画面差异度不下降。
    """
    ev = lambda v: int(v) & ~1  # 取偶数，yuv420p 要求
    W, H = ev(meta["w"]), ev(meta["h"])

    # ---- 画面层：放大 → 非对称裁切 → 回填原尺寸 → 镜像 → 旋转 → 调色 → 噪点 → 锐化
    z = p["zoom"]
    cw, ch = ev(W * p["crop_x"]), ev(H * p["crop_y"])
    # 放大后画面尺寸
    zw, zh = ev(W * z), ev(H * z)
    cw = min(cw, zw - 2)
    ch = min(ch, zh - 2)
    max_x, max_y = max(zw - cw, 0), max(zh - ch, 0)
    if mirror_on:
        ox, oy = p["off_x"], p["off_y"]
    else:
        # 不镜像时强制非中心裁切（0.12~0.88），避免构图与原片过于接近
        ox = 0.12 + p["off_x"] * 0.76
        oy = 0.12 + p["off_y"] * 0.76
    x, y = ev(max_x * ox), ev(max_y * oy)

    vf = [f"scale={zw}:{zh}:flags=bicubic", f"crop={cw}:{ch}:{x}:{y}"]

    if abs(p["rot"]) > 0.02:
        # 先放大一点点再旋转，转完裁回，避免旋转黑边
        rw, rh = ev(cw * 1.03), ev(ch * 1.03)
        vf.append(f"scale={rw}:{rh}:flags=bicubic")
        vf.append(f"rotate={p['rot']}*PI/180:c=black")
        vf.append(f"crop={cw}:{ch}")
    if p["mirror"] and mirror_on:
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
    # 结构层：变速
    if abs(p["speed"] - 1.0) > 0.0005:
        vf.append(f"setpts=PTS/{p['speed']:.6f}")
    vf.append("setsar=1")          # 重置像素宽高比，避免裁切后 SAR/DAR 畸变
    vf.append("format=yuv420p")
    v = ",".join(vf)

    # ---- 音频层：变调 → 变速补偿 → 音量
    a = ""
    if meta["has_audio"]:
        P, S, V = p["pitch"], p["speed"], p["vol"]
        sr = 48000
        af = [f"aresample={sr}", f"asetrate={int(sr * P)}", f"aresample={sr}"]
        tempo = S / P
        if abs(tempo - 1.0) > 0.0005:
            af.append(f"atempo={tempo:.6f}")
        af.append(f"volume={V:.4f}")
        af.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        a = ",".join(af)
    return v, a


def build_cmd(src: Path, out: Path, p: dict, meta: dict, bgm: Path | None,
              gpu: str = "cpu", mirror_on: bool = True,
              subs: str = "keep", subs_area: float = 0.12) -> list:
    v, a = build_filters(p, meta, mirror_on)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    # 结构层：起点微偏移（改变时长分布与首帧）
    if p["ss"] > 0.02:
        cmd += ["-ss", f"{p['ss']:.3f}"]
    cmd += ["-i", str(src)]

    # ---- 原片硬字幕处理：cover 用画面自身的模糊条盖住字幕带，供后续重新加字幕
    if subs == "cover" and subs_area > 0.02:
        top = 1.0 - subs_area
        # 注意：crop 用 ih，overlay 用 H（=main_h），变量名不同别混用
        vchain = (
            f"[0:v]{v}[main];"
            f"[main]split=2[m1][m2];"
            f"[m2]crop=iw:ih*{subs_area:.4f}:0:ih*{top:.4f},gblur=sigma=28[bar];"
            f"[m1][bar]overlay=0:H*{top:.4f}[v]"
        )
    else:
        vchain = f"[0:v]{v}[v]"

    if bgm and meta["has_audio"]:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        fc = (
            f"{vchain};"
            f"[0:a]{a}[a0];"
            f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume=0.22[bg];"
            f"[a0][bg]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
    elif meta["has_audio"]:
        cmd += ["-filter_complex", f"{vchain};[0:a]{a}[a]", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", vchain, "-map", "[v]", "-an"]

    # ---- 编码层：GPU 硬件编码 or CPU
    # 质量档位映射：实测 libx264 crf 23 ≈ h264_nvenc cq 30
    cq = min(51, max(1, p["crf"] + 7))
    if gpu == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", str(cq), "-b:v", "0", "-tune", "hq",
                "-profile:v", "high"]
    elif gpu == "qsv":
        cmd += ["-c:v", "h264_qsv", "-preset", "medium",
                "-global_quality", str(cq), "-profile:v", "high"]
    elif gpu == "amf":
        cmd += ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", str(cq), "-qp_p", str(cq)]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(p["crf"]),
                "-profile:v", "high", "-level", "4.1"]
    cmd += [
        "-g", str(p["gop"]), "-keyint_min", str(max(1, p["gop"] // 2)),
        "-pix_fmt", "yuv420p",
    ]
    if meta["has_audio"]:
        cmd += ["-c:a", "aac", "-b:a", p["abitrate"], "-ar", "48000", "-ac", "2"]
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
        "-t", f"{(meta['duration'] - p['ss']) / p['speed'] + 1.0:.3f}",
        str(out),
    ]
    return cmd


# ---------------------------------------------------------------- 单任务
def render_one(src: Path, out_dir: Path, idx: int, mode: str, seed: int,
               bgm: Path | None, meta: dict, tag: str, log: list,
               gpu: str = "cpu", mirror_on: bool = True,
               subs: str = "keep", subs_area: float = 0.12) -> dict:
    rng = random.Random(seed)
    p = make_params(rng, mode)
    base = f"{tag}_{src.stem}_v{idx:02d}" if tag else f"{src.stem}_v{idx:02d}"
    out = out_dir / f"{base}.mp4"
    n = 1
    while out.exists():
        n += 1
        out = out_dir / f"{base}_{n}.mp4"

    def run(g):
        return subprocess.run(
            build_cmd(src, out, p, meta, bgm, gpu=g, mirror_on=mirror_on,
                      subs=subs, subs_area=subs_area),
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
        )

    t0 = time.time()
    r = run(gpu)
    used_gpu = gpu
    # GPU 编码失败（显存不足/驱动问题）自动回退 CPU，保证任务不中断
    if r.returncode != 0 and gpu != "cpu":
        if out.exists():
            try:
                out.unlink()
            except Exception:
                pass
        r = run("cpu")
        used_gpu = "cpu"
    cost = time.time() - t0
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 4096

    rec = {
        "源文件": src.name,
        "版本": f"v{idx:02d}",
        "输出文件": out.name,
        "状态": "OK" if ok else "失败",
        "耗时秒": f"{cost:.1f}",
        "放大倍数": f"{p['zoom']:.4f}",
        "裁切比X": f"{p['crop_x']:.4f}",
        "裁切比Y": f"{p['crop_y']:.4f}",
        "裁切偏移X": f"{p['off_x']:.3f}",
        "裁切偏移Y": f"{p['off_y']:.3f}",
        "镜像": ("是" if p["mirror"] else "否") if mirror_on else "关(有字幕)",
        "旋转度": f"{p['rot']:.2f}",
        "亮度": f"{p['bright']:.4f}",
        "对比度": f"{p['contrast']:.4f}",
        "饱和度": f"{p['satur']:.4f}",
        "色相": f"{p['hue']:.2f}",
        "噪点": p["noise"],
        "锐化": f"{p['sharp']:.2f}",
        "变速倍率": f"{p['speed']:.5f}",
        "变调倍率": f"{p['pitch']:.5f}",
        "音量": f"{p['vol']:.4f}",
        "起点偏移秒": f"{p['ss']:.3f}",
        "CRF": p["crf"],
        "质量档位": (p["crf"] + 7) if used_gpu != "cpu" else p["crf"],
        "GOP": p["gop"],
        "音频码率": p["abitrate"],
        "编码器": used_gpu,
        "字幕处理": subs,
        "BGM": bgm.name if bgm else "",
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

    gpu = jobs_cfg.get("gpu", "auto")
    if gpu == "auto":
        gpu, _ = detect_gpu(FFMPEG)
    jobs = jobs_cfg["jobs"]
    if jobs is None:
        jobs = 4 if gpu != "cpu" else 2
    jobs = max(1, min(12, jobs))

    print("=" * 62)
    print("  FB 短剧素材批量去重工具")
    print("=" * 62)
    print(f"  输入      : {jobs_cfg['input']}")
    print(f"  视频数量  : {len(srcs)}")
    print(f"  每视频版本: {jobs_cfg['versions']}")
    print(f"  强度档位  : {jobs_cfg['mode']}")
    gpu_txt = {"nvenc": "NVIDIA NVENC (GPU)", "qsv": "Intel QSV (GPU)",
               "amf": "AMD AMF (GPU)", "cpu": "CPU libx264"}.get(gpu, gpu)
    print(f"  编码加速  : {gpu_txt}")
    print(f"  并发任务  : {jobs}")
    print(f"  镜像      : {'开' if jobs_cfg.get('mirror') else '关（保护原片字幕）'}")
    subs = jobs_cfg.get("subs", "keep")
    print(f"  字幕处理  : {subs}" +
          (f"（底部 {jobs_cfg.get('subs_area', 0.12)*100:.0f}%）" if subs == "cover" else ""))
    print(f"  BGM       : {len(bgms)} 个" if bgms else "  BGM       : 未启用")
    print(f"  账号列表  : {len(accounts)} 个" if accounts else "  账号列表  : 未提供")
    print(f"  输出目录  : {out_dir}")
    print("=" * 62, flush=True)

    total = len(srcs) * jobs_cfg["versions"]
    print(f"  预计输出 {total} 条素材，开始处理...\n", flush=True)

    log, mapping = [], []
    tasks = []
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
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = [
            pool.submit(render_one, s, out_dir, i, jobs_cfg["mode"], sd, bg,
                        mt, jobs_cfg["tag"], log, gpu, jobs_cfg.get("mirror", False),
                        jobs_cfg.get("subs", "keep"), jobs_cfg.get("subs_area", 0.12))
            for (s, mt, i, sd, bg) in tasks
        ]
        for _ in as_completed(futs):
            pass

    cost = time.time() - t0
    ok_n = sum(1 for r in log if r["状态"] == "OK")

    # 账号分配表
    if accounts:
        ok_files = [r["输出文件"] for r in log if r["状态"] == "OK"]
        for n, f in enumerate(ok_files):
            mapping.append({"账号": accounts[n % len(accounts)], "分配素材": f, "轮次": n // len(accounts) + 1})

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
    print(f"  完成：成功 {ok_n} / {len(log)} 条，耗时 {cost:.1f} 秒")
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
    v = input(msg).strip().strip('"').strip("'")
    return v if v else default


def interactive(prefill: str = ""):
    print("=" * 62)
    print("  FB 短剧素材批量去重工具  (直接回车 = 用默认值)")
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
    print("     [2] standard 标准 —— 画面+音调+变速（推荐）")
    print("     [3] strong   强力 —— 幅度更大 + 建议配 BGM")
    m = ask("   选择 [默认 2]：", "2")
    mode = {"1": "light", "2": "standard", "3": "strong"}.get(m, "standard")

    print("4) 原片有没有烧进去的硬字幕？（有字幕时不能做镜像，否则字会反）")
    print("     [1] 有字幕 —— 关闭镜像，只靠裁切/调色/旋转做差异（默认）")
    print("     [2] 无字幕 —— 可以镜像（画面差异更大）")
    mir = ask("   选择 [默认 1]：", "1")
    mirror = mir == "2"

    print("5) 原片字幕要不要处理掉？（新规要求字幕重写，遮掉后你自己重新加）")
    print("     [1] 保留不动（默认）")
    print("     [2] 用模糊条盖住底部字幕带（盖完记得自己重新加字幕）")
    sb = ask("   选择 [默认 1]：", "1")
    subs = "cover" if sb == "2" else "keep"
    subs_area = 0.12
    if subs == "cover":
        v = ask("   字幕带占底部多少？[默认 0.12 = 12%]：", "0.12")
        try:
            subs_area = max(0.03, min(0.4, float(v)))
        except ValueError:
            subs_area = 0.12

    out = HERE / "output"
    o = ask(f"6) 输出目录 [默认 {out}]：", str(out))

    bgm_dir = None
    b = ask("7) 是否叠加 BGM？（把文件夹拖进来，不叠加直接回车）：")
    if b and Path(b).exists():
        bgm_dir = Path(b)

    acc = None
    a = ask("8) 账号列表 txt 路径（可选，用于生成分配表，直接回车跳过）：")
    if a:
        p = Path(a) if len(a) > 3 and ("\\" in a or "/" in a) else HERE / "accounts" / a
        if not p.suffix:
            p = p.with_suffix(".txt")
        if p.exists():
            acc = p
        else:
            print(f"   (没找到 {p}，跳过分配表)")

    gpu = "auto"
    print("9) 编码加速：")
    print("     [1] 自动检测 GPU（N卡/A卡/Intel 核显都支持，默认）")
    print("     [2] 只用 CPU")
    g = ask("   选择 [默认 1]：", "1")
    if g == "2":
        gpu = "cpu"

    j = None
    jv = ask("10) 并发任务数 [直接回车 = 自动（GPU 时 4，CPU 时 2）]：", "")
    if jv:
        try:
            j = max(1, min(12, int(jv)))
        except ValueError:
            j = None

    print()
    print("=" * 62)
    print("  输入   :", ip)
    print("  版本数 :", n)
    print("  强度   :", mode)
    print("  镜像   :", "开" if mirror else "关（保护字幕）")
    print("  字幕   :", "模糊遮盖" if subs == "cover" else "保留")
    print("  输出   :", o)
    print("  BGM    :", bgm_dir if bgm_dir else "不使用")
    print("  账号表 :", acc if acc else "不使用")
    print("  加速   :", gpu)
    print("  并发   :", j if j else "自动")
    print("=" * 62)
    if ask("确认开始？(y/n) [默认 y]：", "y").lower() not in ("y", "yes", "是", ""):
        print("已取消。")
        return 0

    return run({
        "input": ip, "output": Path(o), "versions": n, "mode": mode,
        "bgm": bgm_dir, "accounts": acc, "jobs": j,
        "tag": "", "seed": random.randint(1, 10 ** 6),
        "gpu": gpu, "mirror": mirror, "subs": subs, "subs_area": subs_area,
    })


# ---------------------------------------------------------------- CLI
def main():
    ensure_dirs()
    if not FFMPEG:
        print("!! 没找到 ffmpeg。请确认工具目录里有 bin/ffmpeg.exe")
        return 1

    ap = argparse.ArgumentParser(description="FB 短剧素材批量去重工具")
    ap.add_argument("-i", "--input", help="视频文件或文件夹")
    ap.add_argument("-o", "--output", default=str(HERE / "output"), help="输出目录")
    ap.add_argument("-n", "--versions", type=int, default=5, help="每个视频生成几个版本")
    ap.add_argument("--mode", choices=["light", "standard", "strong"], default="standard")
    ap.add_argument("--bgm", help="BGM 文件夹")
    ap.add_argument("--accounts", help="账号列表 txt")
    ap.add_argument("--tag", default="", help="输出文件名前缀（如账号或剧名）")
    ap.add_argument("--jobs", type=int, default=None,
                    help="并发任务数（不填=自动：GPU 4，CPU 2）")
    ap.add_argument("--gpu", choices=["auto", "nvenc", "qsv", "amf", "cpu"], default="auto",
                    help="编码加速：auto 自动检测 / nvenc(N卡) / qsv(Intel) / amf(A卡) / cpu")
    ap.add_argument("--mirror", choices=["on", "off"], default="off",
                    help="是否镜像。原片有硬字幕时必须 off（默认），否则字幕会左右颠倒")
    ap.add_argument("--subs", choices=["keep", "cover"], default="keep",
                    help="原片硬字幕处理：keep 保留 / cover 用模糊条盖住底部字幕带")
    ap.add_argument("--subs-area", type=float, default=0.12,
                    help="cover 模式下的字幕带高度占比（默认 0.12 = 底部 12%%）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（同种子结果可复现）")
    ap.add_argument("--interactive", action="store_true", help="交互式向导")
    args = ap.parse_args()

    if args.interactive or not args.input:
        return interactive(args.input or "")

    return run({
        "input": Path(args.input), "output": Path(args.output),
        "versions": max(1, min(50, args.versions)), "mode": args.mode,
        "bgm": Path(args.bgm) if args.bgm else None,
        "accounts": Path(args.accounts) if args.accounts else None,
        "jobs": args.jobs, "tag": args.tag,
        "seed": args.seed if args.seed is not None else random.randint(1, 10 ** 6),
        "gpu": args.gpu, "mirror": args.mirror == "on",
        "subs": args.subs, "subs_area": max(0.03, min(0.4, args.subs_area)),
    })


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
