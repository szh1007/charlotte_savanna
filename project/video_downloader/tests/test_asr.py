"""ASR 模块单测: SenseVoiceSmall 模型使用 (加载参数 / 调用参数 / 时间戳解析).

不触网、不加载真实模型 (funasr AutoModel 惰性 import + 假模型对象替换),
验证:
- _load_model: AutoModel 加载参数 (本地模型路径优先 / vad_model=fsmn-vad /
  device=cpu / 单例双检 / funasr 缺失报错)
- _infer: model.generate 调用参数 (output_timestamp / sentence_timestamp
  等真实参数), 结果经 _to_segments 解析
- _to_segments: sentence_info (VAD 句子级, 毫秒) / 词级 timestamp 首尾跨度 /
  无时间戳连续窗口兜底
- _clean_text: 官方 rich 清洗优先, 回退手写标签清洗

回归背景: 曾只传 whisper 风格的 sentence_timestamp 且未加载 vad_model,
SenseVoice 不输出 timestamp, 整段音频只出一条无时间字幕 (BV1tBbf6pE5Y
缓存样例 start=0 end=1 一段).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from backend import asr, config


@pytest.fixture(autouse=True)
def reset_model_singleton():
    """每个测试前重置模块级模型单例与假模型类状态 (跨测试累积串扰)."""
    asr._model = None
    FakeAutoModel.instances = []
    FakeAutoModel.generated = []
    yield
    asr._model = None


def _hide_funasr(monkeypatch) -> None:
    """模拟 funasr 整体不可用: 清空 sys.modules 缓存再置 None.

    仅 setitem None 不可靠: 子模块 (如 funasr.utils.postprocess_utils)
    已被 import 时 from 导入直接命中缓存, 不触发父包 halt.
    """
    for name in [
        m for m in list(sys.modules) if m == "funasr" or m.startswith("funasr.")
    ]:
        sys.modules.pop(name, None)
    monkeypatch.setitem(sys.modules, "funasr", None)


class FakeAutoModel:
    """记录 AutoModel 构造参数并返回假模型 (含 record 回传)."""

    instances: list[dict] = []
    generated: list[dict] = []

    def __init__(self, **kwargs):
        FakeAutoModel.instances.append(kwargs)
        self.kwargs = kwargs

    def generate(self, **kwargs):
        FakeAutoModel.generated.append(kwargs)
        return self.kwargs.get("result", [])


# ----- _load_model: AutoModel 加载参数 -----


def test_load_model_prefers_local_dir_and_enables_vad(monkeypatch, model_assets):
    """本地模型目录就绪时加载本地路径, 且必须挂载 fsmn-vad (句子级时间戳前提).

    双模型统一管理: 主模型与 VAD 模型均优先项目 models/ 本地目录.
    """
    monkeypatch.setattr("funasr.AutoModel", FakeAutoModel)
    model = asr._load_model()
    (kwargs,) = FakeAutoModel.instances
    assert kwargs["model"] == str(model_assets["model_dir"])
    assert kwargs["vad_model"] == str(model_assets["vad_model_dir"])
    assert kwargs["device"] == "cpu"
    assert model is asr._load_model()  # 单例: 重复调用不重复加载


def test_load_model_vad_falls_back_to_modelscope_id(monkeypatch, model_assets):
    """VAD 模型未下载 (本地目录缺失) 时回退 modelscope id (funasr 自动下载)."""
    for f in model_assets["vad_model_dir"].iterdir():
        f.unlink()
    model_assets["vad_model_dir"].rmdir()
    monkeypatch.setattr("funasr.AutoModel", FakeAutoModel)
    asr._load_model()
    (kwargs,) = FakeAutoModel.instances
    assert kwargs["vad_model"] == config.ASR_VAD_MODEL
    assert kwargs["model"] == str(model_assets["model_dir"])  # 主模型不受影响


def test_load_model_falls_back_to_modelscope_id(monkeypatch, model_assets):
    """本地模型未下载 (目录不存在) 时回退 modelscope 模型 id (funasr 自动下载缓存)."""
    for f in model_assets["model_dir"].iterdir():
        f.unlink()
    model_assets["model_dir"].rmdir()
    monkeypatch.setattr("funasr.AutoModel", FakeAutoModel)
    asr._load_model()
    (kwargs,) = FakeAutoModel.instances
    assert kwargs["model"] == config.ASR_MODEL  # iic/SenseVoiceSmall


def test_load_model_single_load_under_double_check(monkeypatch, model_assets):
    """双检锁保证并发首载只加载一次 (模型全局单例)."""
    monkeypatch.setattr("funasr.AutoModel", FakeAutoModel)
    asr._load_model()
    asr._load_model()
    assert len(FakeAutoModel.instances) == 1


def test_load_model_missing_funasr(monkeypatch, model_assets):
    """funasr 未安装时报可读错误, 提示 pip install funasr."""
    _hide_funasr(monkeypatch)
    with pytest.raises(asr.TranscriptError, match=r"funasr.*未安装"):
        asr._load_model()


def test_load_model_autoload_failure(monkeypatch, model_assets):
    """AutoModel 构造失败 (模型损坏/下载失败) 报 ASR 模型加载失败."""

    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("funasr.AutoModel", Boom)
    with pytest.raises(asr.TranscriptError, match="ASR 模型加载失败: boom"):
        asr._load_model()


# ----- _infer: model.generate 调用参数 -----


def test_infer_passes_real_generate_params(monkeypatch, model_assets):
    """generate 必须携带句子级时间戳参数并解析为带时间戳段.

    回归: 曾只传 whisper 风格 sentence_timestamp 且未加载 vad_model,
    SenseVoice 不输出时间戳, 整段音频只出一条无时间字幕. 不得同时传
    output_timestamp (无 punc_model 时 pipeline 跳过句子分割, 实测
    sentence_info 为空).
    """
    fake = FakeAutoModel(
        result=[
            {
                "text": "<|zh|><|NEUTRAL|><|Speech|>你好世界",
                "sentence_info": [
                    {
                        "start": 1200,
                        "end": 2600,
                        "sentence": "<|zh|><|NEUTRAL|>你好世界",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(asr, "_load_model", lambda: fake)
    segments = asr._infer(Path("fake.wav"))
    assert fake.generated[0]["input"] == "fake.wav"
    assert fake.generated[0]["cache"] == {}
    assert fake.generated[0]["use_itn"] is True
    assert fake.generated[0]["batch_size_s"] == 60
    assert fake.generated[0]["sentence_timestamp"] is True  # pipeline 层句子分割
    assert "output_timestamp" not in fake.generated[0]  # 与句子分割互斥
    assert segments == [
        {"start": 1.2, "end": 2.6, "text": "你好世界", "ts_estimated": False}
    ]


# ----- _to_segments: 时间戳解析优先级 -----


def test_to_segments_prefers_sentence_info(monkeypatch, model_assets):
    """vad_model 输出的 sentence_info (毫秒) 优先: 逐句转秒 + 清洗元标签."""
    result = [
        {
            "text": "<|zh|><|NEUTRAL|><|Speech|>第一句第二句",
            "sentence_info": [
                {"start": 0, "end": 1200, "sentence": "<|zh|><|NEUTRAL|>第一句"},
                {"start": 1200, "end": 2500, "sentence": "<|zh|><|NEUTRAL|>第二句"},
            ],
        }
    ]
    assert asr._to_segments(result) == [
        {"start": 0.0, "end": 1.2, "text": "第一句", "ts_estimated": False},
        {"start": 1.2, "end": 2.5, "text": "第二句", "ts_estimated": False},
    ]


def test_to_segments_word_timestamp_full_span(monkeypatch, model_assets):
    """无 sentence_info 时用词级 timestamp 首尾跨度 (旧实现只取 ts[0] 截断 end)."""
    result = [
        {
            "text": "<|zh|><|NEUTRAL|>你好世界",
            "timestamp": [[1000, 1200], [1200, 1500], [1500, 3000]],
        }
    ]
    assert asr._to_segments(result) == [
        {"start": 1.0, "end": 3.0, "text": "你好世界", "ts_estimated": False}
    ]


def test_to_segments_fallback_continuous_window(monkeypatch, model_assets):
    """模型异常无任何时间戳时连续窗口兜底, 不丢文本 (句长估算 1s/8 字)."""
    result = [{"text": "<|zh|>一二三四五六七八九十"}]
    segments = asr._to_segments(result)
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == pytest.approx(10 / 8, rel=1e-6)  # len=10 字
    assert segments[0]["ts_estimated"] is True  # 估算时间戳: 润色线性重算


def test_to_segments_skips_empty_and_malformed(monkeypatch, model_assets):
    """空文本 / 非 dict 被过滤; 无文本 sentence_info 段回退 item 级文本兜底."""
    result = [
        None,
        {"text": "   "},
        {"text": "<|zh|>只有一句", "sentence_info": [{"start": 100, "end": 900}]},
    ]
    assert asr._to_segments(result) == [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "只有一句",
            "ts_estimated": True,
        }  # 兜底窗口: len=4 → 4/8 < 1
    ]


def test_to_segments_empty_sentence_info_falls_back_to_text(monkeypatch, model_assets):
    """sentence_info 全空文本时回退 item 级 text (含词级时间戳)."""
    result = [
        {
            "text": "<|zh|>兜底文本",
            "sentence_info": [{"start": 100, "end": 200, "sentence": ""}],
            "timestamp": [[500, 1500]],
        }
    ]
    assert asr._to_segments(result) == [
        {"start": 0.5, "end": 1.5, "text": "兜底文本", "ts_estimated": False}
    ]


# ----- _clean_text: 元标签清洗 -----


def test_clean_text_prefers_official_rich_postprocess(monkeypatch):
    """funasr 可用时用官方 rich_transcription_postprocess (标签覆盖全)."""
    import funasr.utils.postprocess_utils as ppu

    called = []

    def fake_rich(text: str) -> str:
        called.append(text)
        return "清洗后"

    monkeypatch.setattr(ppu, "rich_transcription_postprocess", fake_rich)
    assert asr._clean_text("<|zh|><|NEUTRAL|>原始") == "清洗后"
    assert called == ["<|zh|><|NEUTRAL|>原始"]


def test_clean_text_legacy_fallback(monkeypatch):
    """funasr 不可用 (官方清洗导入失败) 时回退手写标签清洗, 模块仍可导入."""
    _hide_funasr(monkeypatch)
    assert asr._clean_text("<|zh|><|HAPPY|>你好  <|en|>world") == "你好 world"
