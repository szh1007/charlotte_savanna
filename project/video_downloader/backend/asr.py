"""ASR 转写兜底 (ADR-0005): SenseVoice (funasr) 本地转写, 无登录依赖.

链路: 下载音频流 (yt-dlp bestaudio) → ffmpeg 分片 (16k 单声道 wav,
每片 ASR_CHUNK_SECONDS) → 逐片 SenseVoice 转写 → 合并为
[{start, end, text}] 时间戳文本 (Transcript 原料).

funasr 惰性 import + 模型惰性加载 (首次自动下载约 1GB): 未安装 funasr
时模块可导入, 调用才明确报错 (测试 mock 本模块, 无需真实 ASR 依赖).
模型推理串行化 (模块级锁): SenseVoice 非线程安全, 多 worker 并发转写
时排队, 学习项目可接受.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from . import config

# 进度回调: (stage, pct, msg), stage 为 "audio" | "transcribe", pct 0~1 段内
ProgressCb = Callable[[str, float, str], None]

# 模型单例 + 推理锁: 首次加载 (下载模型) 与逐片推理均在此锁内串行
_model_lock = threading.Lock()
_model = None


class TranscriptError(Exception):
    """转录失败 (音频下载 / 分片 / 转写), 原因透传."""


def _friendly(message: str, fallback: str) -> str:
    """去掉 yt-dlp 异常的 ERROR: 前缀, 保留可读原因."""
    msg = str(message)
    if msg.startswith("ERROR: "):
        msg = msg[len("ERROR: ") :]
    return msg or fallback


def transcribe(
    url: str,
    progress_cb: ProgressCb,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """下载音频并转写为 [{start, end, text}]; 失败抛 TranscriptError.

    音频为中间产物, 临时目录用完即删 (异常路径由调用方清理兜底).
    cancel_event 置位时在分片间隙中断 (与下载任务的取消信号同一模式).
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="asr_", dir=config.DOWNLOADS_DIR))
    try:
        audio = _download_audio(url, tmpdir, progress_cb, cancel_event)
        chunks = _split_audio(audio, tmpdir)
        segments: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptError("已取消")
            pieces = _infer(chunk)
            offset = idx * config.ASR_CHUNK_SECONDS
            for piece in pieces:
                piece["start"] += offset
                piece["end"] += offset
            segments.extend(pieces)
            pct = (idx + 1) / len(chunks)
            progress_cb("transcribe", pct, f"转写中 {idx + 1}/{len(chunks)} 片")
        return segments
    except TranscriptError:
        raise
    except Exception as e:
        raise TranscriptError(f"转写失败: {e}") from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # 清理中间音频与分片


def _download_audio(
    url: str,
    tmpdir: Path,
    progress_cb: ProgressCb,
    cancel_event: threading.Event | None = None,
) -> Path:
    """yt-dlp 下载最佳音频流到临时目录, 返回文件路径.

    独立的引擎调用点: 测试通过替换本函数 mock 音频下载.
    """

    def hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadError("已取消")
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        progress_cb("audio", (done / total) if total else 0.0, "正在下载音频")

    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(tmpdir / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "progress_hooks": [hook],
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as e:
        raise TranscriptError(_friendly(e, "音频下载失败")) from e
    requested = (info or {}).get("requested_downloads") or []
    path = requested[0].get("filepath") if requested else None
    if not path or not Path(path).is_file():
        raise TranscriptError("音频下载完成但无法定位文件")
    return Path(path)


def _split_audio(src: Path, tmpdir: Path) -> list[Path]:
    """ffmpeg 分片为 16k 单声道 wav (SenseVoice 输入格式), 返回有序片列表."""
    pattern = tmpdir / "chunk_%03d.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "segment",
        "-segment_time",
        str(config.ASR_CHUNK_SECONDS),
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TranscriptError(f"音频分片失败 (ffmpeg): {result.stderr.strip()}")
    chunks = sorted(tmpdir.glob("chunk_*.wav"))
    if not chunks:
        raise TranscriptError("音频分片失败: 无输出分片")
    return chunks


def _infer(audio_path: Path) -> list[dict[str, Any]]:
    """单片 SenseVoice 转写 → [{start, end, text}] (片内时间, 秒).

    sentence_timestamp=True 触发 AutoModel pipeline 层 (需 vad_model
    配合) 生成句子级 sentence_info (毫秒, VAD 段边界为句子时间戳).
    不能同时传 output_timestamp=True: 有词级 timestamp 且无 punc_model
    时 pipeline 跳过句子分割, sentence_info 为空 (实测验证).
    """
    model = _load_model()
    with _model_lock:  # 推理串行: 模型非线程安全
        result = model.generate(
            input=str(audio_path),
            cache={},
            use_itn=True,
            batch_size_s=60,
            sentence_timestamp=True,
        )
    return _to_segments(result)


def _load_model():
    """惰性加载 SenseVoice 模型 (首次下载约 1GB, 全局单例)."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # 双检: 并发首载只加载一次
            return _model
        try:
            from funasr import AutoModel
        except ModuleNotFoundError as e:
            if e.name == "funasr":
                raise TranscriptError(
                    "ASR SDK (funasr) 未安装, 请执行 pip install funasr "
                    "(首次使用自动下载 SenseVoice 模型约 1GB)"
                ) from e
            # funasr 已装但其依赖缺失 (如 torchaudio): 报真实缺失模块, 避免误导
            raise TranscriptError(
                f"ASR SDK (funasr) 依赖缺失: 未找到模块 {e.name}, "
                "请安装对应依赖 (如 pip install torchaudio)"
            ) from e
        except ImportError as e:
            raise TranscriptError(f"ASR SDK (funasr) 导入失败: {e}") from e
        try:
            # 优先加载项目 models/ 本地模型 (ADR-0006 预下载产物, funasr
            # AutoModel 支持本地路径), 未下载时回退 modelscope 模型 id
            # (旧行为: 自动下载到 modelscope 缓存).
            # vad_model 必须有: SenseVoice 本身无句子边界能力, 缺 VAD 时整段
            # 音频按一条句子转写 (只出 1 段, 无时间戳), VAD 分段 +
            # sentence_timestamp 才能产出句子级带时间戳字幕 (官方 README
            # 推荐组合). VAD 与主模型同走项目 models/ 本地目录 (统一下载
            # 管理), 目录缺失回退 modelscope id
            model_dir = config.MODELS_DIR / config.MODEL_DIR_NAME
            model_ref = str(model_dir) if model_dir.is_dir() else config.ASR_MODEL
            vad_dir = config.MODELS_DIR / config.MODEL_VAD_DIR_NAME
            vad_ref = str(vad_dir) if vad_dir.is_dir() else config.ASR_VAD_MODEL
            # vad_kwargs: 断句粒度可调 (台词间隔短时 VAD 把两句合并为一条
            # 字幕; 调低 max_end_silence_time 可断出更多短句, .env 可配)
            vad_kwargs: dict[str, Any] = {
                "max_end_silence_time": config.ASR_VAD_MAX_END_SILENCE_MS
            }
            if config.ASR_VAD_SPEECH_TO_SIL_THRES_MS:
                vad_kwargs["speech_to_sil_time_thres"] = int(
                    config.ASR_VAD_SPEECH_TO_SIL_THRES_MS
                )
            _model = AutoModel(
                model=model_ref,
                vad_model=vad_ref,
                device="cpu",
                vad_kwargs=vad_kwargs,
            )
        except Exception as e:
            raise TranscriptError(f"ASR 模型加载失败: {e}") from e
        return _model


def _to_segments(result: Any) -> list[dict[str, Any]]:
    """funasr 输出 → [{start, end, text, ts_estimated}] (毫秒转秒, 清洗元标签).

    优先级: ① sentence_info (vad_model 输出的句子级时间戳, 官方推荐字段,
    每句一条) → ② timestamp (词级时间戳列表, 取首词 start ~ 尾词 end,
    覆盖全句跨度) → ③ 无时间戳兜底 (句长估算连续窗口, 不丢文本).
    SenseVoice 每句输出带 <|lang|> <|emotion|> 等元标签, 需清洗.
    ts_estimated 为时间戳来源标记 (内部字段, 对外序列化被 schema 过滤):
    ③ 兜底估算置 True, 字幕润色据此走「保留原时间戳」或「线性均匀重算」
    (用户反馈: 估算时间戳无真实口播节奏, 保留无意义).
    """
    segments: list[dict[str, Any]] = []
    cursor = 0.0
    for item in result or []:
        if not isinstance(item, dict):
            continue
        start = end = None
        # ① 句子级 (AutoModel + vad_model): sentence_info 元素为
        # {start, end, sentence/text}, 时间戳毫秒
        for sent in item.get("sentence_info") or []:
            if not isinstance(sent, dict):
                continue
            text = _clean_text(sent.get("sentence") or sent.get("text") or "")
            if not text:
                continue
            s, e = _ms_times(sent.get("start"), sent.get("end"))
            start, end = s, e
            segments.append(
                {
                    "start": round(s, 2),
                    "end": round(e, 2),
                    "text": text,
                    "ts_estimated": False,
                }
            )
            cursor = e
        if segments and start is not None:
            continue
        text = _clean_text(item.get("text") or "")
        if not text:
            continue
        # ② 词级 timestamp: [[start_ms, end_ms], ...] 每个词一段,
        # 全句跨度 = 首词 start ~ 尾词 end (旧实现只取 ts[0], end 截断)
        ts = item.get("timestamp")
        has_ts = isinstance(ts, list) and ts and isinstance(ts[0], list | tuple)
        if has_ts:
            start = float(ts[0][0]) / 1000.0
            end = float(ts[-1][1]) / 1000.0
        else:  # ③ 无时间戳兜底: 连续窗口 (句长估算 1s/8 字)
            start = cursor
            end = cursor + max(len(text) / 8.0, 1.0)
        segments.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "text": text,
                "ts_estimated": not has_ts,
            }
        )
        cursor = end
    return segments


def _ms_times(start: Any, end: Any) -> tuple[float, float]:
    """毫秒时间戳 → 秒; 缺失/非法时 (0, 0) 占位 (调用方兜底)."""
    try:
        return float(start or 0) / 1000.0, float(end or 0) / 1000.0
    except (TypeError, ValueError):
        return 0.0, 0.0


def _clean_text(text: str) -> str:
    """清洗 SenseVoice 元标签并规范空白.

    优先官方 rich_transcription_postprocess (funasr.utils.postprocess_utils,
    官方维护, 标签覆盖全); funasr 未安装时回退手写标签清洗, 保持本模块
    可导入 (与 _load_model 惰性 import 同一约定).
    """
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        return rich_transcription_postprocess(text)
    except ImportError:
        return _legacy_clean(text)


def _legacy_clean(text: str) -> str:
    """手写标签清洗 (funasr 不可用时的回退)."""
    for tag in (
        "<|zh|>",
        "<|en|>",
        "<|ja|>",
        "<|ko|>",
        "<|yue|>",
        "<|NEUTRAL|>",
        "<|HAPPY|>",
        "<|SAD|>",
        "<|ANGRY|>",
        "<|SURPRISE|>",
        "<|FEAR|>",
        "<|DISGUST|>",
        "<|emotion|>",
    ):
        text = text.replace(tag, "")
    return " ".join(text.split())
