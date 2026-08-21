"""应用配置: 从 .env 读取环境变量 (模板见 .env.example)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 (backend/ 的上一级)
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载子项目独立 .env (不提交)
load_dotenv(BASE_DIR / ".env")

# 交付文件目录 (TTL 清理范围, T02+ 使用)
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", BASE_DIR / "downloads"))

# 模型下载目录 (ADR-0006): 语音转写模型本体, 持久资产不清理 (env 可覆盖)
MODELS_DIR = Path(os.getenv("MODELS_DIR", BASE_DIR / "models"))

# 模型字幕缓存目录 (ADR-0006): 转录段 JSON 按 TTL 清理, 与交付 TTL 同源
SUBTITLES_DIR = Path(os.getenv("SUBTITLES_DIR", MODELS_DIR / "subtitles"))

# 会员密钥: 校验通过解锁会员档能力 (空 = 未配置, 拒绝一切提交)
MEMBER_KEY = os.getenv("MEMBER_KEY", "")

# 交付直链有效期 (秒): 免费 24h / 会员 72h (PRD §5, T06 清理判定依据)
FREE_DELIVERY_TTL = float(os.getenv("FREE_DELIVERY_TTL", 24 * 3600))
MEMBER_DELIVERY_TTL = float(os.getenv("MEMBER_DELIVERY_TTL", 72 * 3600))


def delivery_ttl(is_member: bool) -> float:
    """交付直链有效期按创建者身份计算 (免费 24h / 会员 72h, PRD §5).

    单一来源: cleaner 过期判定、任务 expires_at 序列化共用, 避免各自
    复制身份分支导致判定漂移.
    """
    return MEMBER_DELIVERY_TTL if is_member else FREE_DELIVERY_TTL


# ----- AI 总结 (ADR-0005) -----

# 服务端自备 B 站 cookie (字幕快路径, 可选): 配置且有效时优先提取官方字幕,
# 留空 / 无字幕 / 失败自动回退 SenseVoice 转写. 敏感信息, 仅 .env 维护,
# 不收集用户任何凭据
BILI_COOKIE = os.getenv("BILI_COOKIE", "")

# LLM 调用 (DeepSeek, openai 兼容 SDK): 子项目 .env 未配置 LLM_* 时回退
# DEEPSEEK_* (可复用仓库根 .env 的 key, 与 LangChain 生态同源)
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv(
    "LLM_MODEL", os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-v4-flash")
)

# ASR 转写模型 (SenseVoice): 下载到项目 models/ 目录 (ADR-0006), 模型未下载时
# 回退 modelscope 模型 id (自动下载缓存, 旧行为)
ASR_MODEL = os.getenv("ASR_MODEL", "iic/SenseVoiceSmall")
# 本地模型目录名 (models/SenseVoiceSmall/ 下 config.yaml + model.pt 即 ready)
MODEL_DIR_NAME = "SenseVoiceSmall"

# ASR VAD 分段模型 (fsmn-vad): SenseVoice 句子级时间戳必需 (无 VAD 整段音频
# 只出一条无时间字幕), 同样下载到项目 models/ 统一管理 (ADR-0006 同源)
ASR_VAD_MODEL = os.getenv(
    "ASR_VAD_MODEL", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
)
# 本地 VAD 模型目录名 (models/fsmn-vad/ 下 config.yaml + model.pt 即 ready)
MODEL_VAD_DIR_NAME = "fsmn-vad"

# ASR 分片长度 (秒): 每片转写后上报一次进度, 长音频避免单次调用内存峰值
ASR_CHUNK_SECONDS = float(os.getenv("ASR_CHUNK_SECONDS", 600))

# 免费档每日配额 (按匿名 client_id + 日窗口计数, 内存态重启清零, ADR-0005):
# 会员不限; 与「免费档真实受限」哲学一致
FREE_SUMMARY_DAILY = int(os.getenv("FREE_SUMMARY_DAILY", 3))
FREE_QA_DAILY = int(os.getenv("FREE_QA_DAILY", 10))
