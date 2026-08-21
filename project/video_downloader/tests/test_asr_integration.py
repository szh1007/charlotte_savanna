"""ASR 真实模型集成测试: SenseVoiceSmall + fsmn-vad 多段带时间戳字幕验收.

与 test_asr.py (mock, 不触网不加载模型) 互补: 本文件加载 models/ 预下载
的真实模型 (ADR-0006) 转写官方示例音频, 验证句子级时间戳链路真实可用.
验收目标 (用户反馈): 缺 VAD 时整段音频只出一条无时间字幕; 挂载 fsmn-vad
后必须产出 ≥2 段、时间戳递增且非估算的真实字幕 (润色「保留原时间戳」
分支的前提).

运行慢 (CPU 加载 ~1GB 模型 + 推理约 1~3 分钟), 默认跳过; 显式运行:
    .venv/Scripts/python -m pytest tests/test_asr_integration.py -m integration -v
模型文件缺失 (未执行模型下载) 时自动跳过, 不阻塞常规测试.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from backend import asr, config

pytestmark = pytest.mark.integration

# SenseVoiceSmall 模型自带官方示例音频 (zh/en/ja/ko/yue, 与模型同目录).
# 单个示例是连续一句话 (实测 zh.mp3 整段无停顿, VAD 只出 1 个语音段),
# 拼接全部示例 + 段间插静音才能验证 VAD 分段 → 多段真实时间戳
EXAMPLE_DIR = (
    Path(__file__).resolve().parents[1] / "models" / config.MODEL_DIR_NAME / "example"
)
EXAMPLE_AUDIOS = ("zh.mp3", "en.mp3", "ja.mp3", "ko.mp3", "yue.mp3")


@pytest.fixture(autouse=True)
def use_real_models(monkeypatch):
    """覆盖 conftest.model_assets 隔离: 集成测试必须加载真实 models/ 模型.

    conftest 的 model_assets (autouse) 把 MODELS_DIR 指向 tmp 伪模型目录
    (config.yaml 内容 model: fake), 真实加载会注册失败; 本 fixture 在
    conftest 之后执行 (模块内 autouse 晚于高层), 重新指回项目真实
    models/ 目录 (SenseVoiceSmall + fsmn-vad 预下载产物). 模型缺失
    (未执行模型下载) 时跳过, 不阻塞常规测试.
    """
    models_dir = Path(__file__).resolve().parents[1] / "models"
    monkeypatch.setattr(config, "MODELS_DIR", models_dir)
    monkeypatch.setattr(config, "SUBTITLES_DIR", models_dir / "subtitles")
    if not _model_ready():
        pytest.skip("本地模型未下载 (models/ 缺 SenseVoiceSmall / fsmn-vad)")


def _model_ready() -> bool:
    """本地双模型 (转写 + VAD) 均就绪才跑集成测试 (文件为唯一事实源)."""
    return all(
        (config.MODELS_DIR / d / f).is_file()
        for d in (config.MODEL_DIR_NAME, config.MODEL_VAD_DIR_NAME)
        for f in ("config.yaml", "model.pt")
    )


def _build_multi_segment_audio(tmpdir: Path) -> Path:
    """拼接全部官方示例音频 (段间插 0.5s 静音) → 多语音段 16k wav.

    VAD 只在静音处分段: 单示例音频是连续一句话, 拼接 + 静音间隔才
    能产出 ≥2 个语音段, 验证 VAD 分段 → 多段真实时间戳字幕.
    """
    missing = [f for f in EXAMPLE_AUDIOS if not (EXAMPLE_DIR / f).is_file()]
    if missing:
        pytest.skip(f"官方示例音频缺失: {missing}")
    out = tmpdir / "multi_16k.wav"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for name in EXAMPLE_AUDIOS:
        cmd += ["-i", str(EXAMPLE_DIR / name)]
    cmd += ["-f", "lavfi", "-i", "aevalsrc=0:d=1.5:s=16000"]  # 1.5s 静音源
    # 每段截 3s (aresample 统一 16k), 段间插入静音后 concat.
    # 静音 ≥1.5s: fsmn-vad 默认 max_end_silence=800ms, 0.5s 静音会被吞
    # (实测 0.5s 间隔整段仍识别为 1 个语音段, 无句子边界)
    filters = [
        f"[{i}:a]aresample=16000,atrim=0:3[a{i}]" for i in range(len(EXAMPLE_AUDIOS))
    ]
    chain = []
    for i in range(len(EXAMPLE_AUDIOS) - 1):
        chain.append(f"[a{i}][{len(EXAMPLE_AUDIOS)}:a]")
    chain.append(f"[a{len(EXAMPLE_AUDIOS) - 1}]")
    n = len(EXAMPLE_AUDIOS) * 2 - 1  # concat 输入数 = 5 语音 + 4 静音
    filters.append("".join(chain) + f"concat=n={n}:v=0:a=1[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"ffmpeg 拼接音频失败 (环境缺 ffmpeg?): {result.stderr.strip()}")
    return out


def test_real_models_produce_multi_segment_timestamped_subtitle(tmp_path) -> None:
    """真实链路验收: SenseVoiceSmall + fsmn-vad 转写拼接示例音频.

    断言 (对应验收目标):
    1. 输出 ≥2 段 — VAD 分段生效, 不再整段只出一条字幕
       (实测真实输出: 缺 VAD 整段一条; VAD 分段 → sentence_info 逐段,
       元素含 {start, end, text, sentence, timestamp}, 毫秒)
    2. 每段 start < end 且按序递增 — 时间戳有效可定位
    3. 全部 ts_estimated=False — 真实 VAD/词级时间戳, 非句长估算兜底
       (润色据此走「保留原时间戳」分支, 不线性重算)
    4. 文本非空 — 元标签清洗后保留有效内容
    """
    wav = _build_multi_segment_audio(tmp_path)
    segments = asr._infer(wav)  # 真实加载 SenseVoiceSmall + fsmn-vad, CPU 推理

    assert len(segments) >= 2, (
        f"期望 VAD 分段产出 ≥2 段带时间戳字幕, 实际 {len(segments)} 段: {segments}"
    )
    starts = [seg["start"] for seg in segments]
    assert starts == sorted(starts), f"时间戳非递增: {starts}"
    for seg in segments:
        assert seg["start"] < seg["end"], f"时间戳非法 (start >= end): {seg}"
        assert seg["ts_estimated"] is False, f"出现估算兜底时间戳: {seg}"
        assert seg["text"].strip(), f"清洗后文本为空: {seg}"
