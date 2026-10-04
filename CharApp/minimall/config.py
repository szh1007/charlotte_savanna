"""minimall 业务包的配置读取: 商城内部端点的地址与共享令牌从哪来.

一句话理解: 环境变量 → 客户端实例的翻译层, 与 `checkpoint/config.py`、
`db/config.py` 同一套做法 —— 变量名在这里声明成常量, 不在业务代码里散着搜
字符串 (审计「谁读了 env」时应当一眼看得见)。

两个变量的值都写在**仓库根** `.env` 里 (与 `CHARPLOT_*` 同一个文件, 不提交;
模板见 `.env.example`)。`CHARAPP_INTERNAL_TOKEN` 必须与商城侧同值 —— 不一样的话
商城一律拒绝 (那边 fail closed), 助手只会报「工具执行时发生内部错误」。

服务进程 (server.py) 的两个变量 (`CHARAPP_SERVER_*`) 也在这里读: 监听地址与
端口是**部署期**配置, 与「助手怎么跟商城说话」是两件事, 但同属「业务读 env」
这一个翻译层, 放一起才看得全。

上下文压缩的五个旋钮 (`CHARAPP_CONTEXT_*`, ticket 18) 同理: 它们是**运行期**的
账目口径 (多大起压 / 留几轮 / 摘要开不开), 与前面几组都不是一件事 —— 但同样是
「业务读 env」, 同样只该有一处翻译。

知识库那条线 (L5) 也在这里读 (`CHARAPP_MILVUS_URL` / `CHARAPP_MODELSCOPE_ROOT` /
`CHARAPP_EMBEDDING_*` / `CHARAPP_RERANKER_*` / 两个 K / `CHARAPP_QUERY_REWRITE`):
向量库地址与两个本地模型的位置是**部署期**配置, 与前面几组同属「业务读 env」这一个
翻译层 —— 它是这一页的最后一段, 不是另一页纸。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from CharApp.minimall.client import DEFAULT_BASE_URL, MinimallClient

# 内部端点前缀 (不给则用本机 Django 开发服务器, 见 DEFAULT_BASE_URL)
ENV_BASE_URL = "CHARAPP_BASE_URL"

# 内部令牌 (与商城侧 CHARAPP_INTERNAL_TOKEN 同值; 两边读的是同一个名字, 但归属
# 不同 —— 商城那份在 settings/base.py, 这里这份在业务侧)
ENV_TOKEN = "CHARAPP_INTERNAL_TOKEN"

# 服务进程的监听地址与端口 (与既有 CHARAPP_* 同族)
ENV_SERVER_HOST = "CHARAPP_SERVER_HOST"
ENV_SERVER_PORT = "CHARAPP_SERVER_PORT"

# 思考模式开关 (**服务端专用**: 命令行入口用 `--no-thinking`, 不看这个变量)
ENV_THINKING = "CHARAPP_THINKING"

# 增量渲染开关 (#66 那条线, C29): 答复是否边收边发 —— 打开后每次模型调用带
# stream=True, 正文增量发成 `answer_delta`、思维链增量发成 `reasoning`.
#
# 与 CHARAPP_THINKING 的差别只在**默认方向**: 那个不填 = 不传 (让上游决定), 这个
# 不填 = **开** —— 它是产品行为 (用户看得见的"逐字"), 服务入口该有它; 关掉是给
# 对照实验与排障留的口子 (`.env` 里写 0). CLI 与跑分**不吃这个变量**: 它们拿的是
# MinimallService.stream 的字段默认值 (False, 见那条注释).
ENV_STREAM = "CHARAPP_STREAM"

# 上下文压缩的五个旋钮 (ticket 18): 多大起压 / 留几轮 / 老工具结果截到多少字 /
# 摘要开不开 / 压到多小才停手.
#
# 为什么带 `CONTEXT_` 这一段而不是 `CHARAPP_COMPACT_*`: 它们管的是「这次请求发给
# 模型的那份上下文有多大」—— 框架那边叫它上下文视图/压缩 (`agent/compaction.py`),
# 而 `CHARAPP_` 前缀表示归属 (这个业务的账), 中间那段说清是哪一件事.
ENV_CONTEXT_MAX_TOKENS = "CHARAPP_CONTEXT_MAX_TOKENS"
ENV_CONTEXT_KEEP_TURNS = "CHARAPP_CONTEXT_KEEP_TURNS"
ENV_CONTEXT_TOOL_LIMIT = "CHARAPP_CONTEXT_TOOL_LIMIT"
ENV_CONTEXT_SUMMARY = "CHARAPP_CONTEXT_SUMMARY"
ENV_CONTEXT_WATERMARK = "CHARAPP_CONTEXT_WATERMARK"

# 跑分模拟买家输的那一次支付密码 (issue 43). **只有离线跑分器读它** —— 生产那两个
# 入口一个字都不看: 真正的买家在恢复请求的正文里输密码 (`server/app.py` 第 5 步),
# 那个值不经过 env.
#
# 它为什么也在这一页 (而不是让 eval 包自己读): 「业务读 env 只该有一处翻译」是本
# 模块立的规矩, 而它读的**就是**这笔业务的支付密码 —— 与 `tools.PAYMENT_PASSWORD_FIELD`
# 是同一个概念, 名字跟着它取. 归属是跑分, 值是商城的凭据, 两句话都写在这一行注释里.
ENV_EVAL_PAYMENT_PASSWORD = "CHARAPP_EVAL_PAYMENT_PASSWORD"

# 布尔配置认得的写法 (跟着 .env 的习惯走: 1/true/yes/on —— 与 db/config.py 同一套)
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

# 默认只监听本机: 这个服务面向同一台机器上的 Django 转发层, 不直接对外
# (浏览器 ↔ Django 才是唯一验证身份的边界, 见 PRD §4.10)。端口取 1007, 与
# PLAN §4 架构图里那个数无关 —— 它是本机默认值, 换环境改 CHARAPP_SERVER_PORT 即可。
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 1007

# 压缩的默认值 (框架给的那套是按「通用」定的, 这一套是按**本业务的账**定的).
#
# 阈值 3.2 万: 本业务的运行预算一共 6 万 token (`service.DEFAULT_MAX_TOTAL_TOKENS`),
# 单请求在 3.2 万起压, 于是压完仍有大半个预算留给后面几轮 —— 比框架默认的 2.4 万
# 宽松, 因为客服问答的历史里工具返回偏多, 压得太勤会把这些结论反复截短.
#
# **为什么阈值不是按「省钱」定的** (ADR-0005 末节): 固定前缀 (身份说明 + 全部工具的
# schema) 命中缓存时几乎免费, 而压掉旧历史会让新前缀**按未命中价重算一次**. 压缩
# 真正买的是**窗口余量与首字延迟**, 不是钱. 所以阈值跟着窗口的富余走, 别拿它当
# 省钱开关调.
DEFAULT_CONTEXT_MAX_TOKENS = 32_000
DEFAULT_CONTEXT_KEEP_TURNS = 6
DEFAULT_CONTEXT_TOOL_LIMIT = 2_000
DEFAULT_CONTEXT_SUMMARY = True
DEFAULT_CONTEXT_WATERMARK = 0.7


class MinimallConfigError(Exception):
    """业务侧配置缺失或非法.

    与框架的 `ModelConfigError` / `CheckpointConfigError` 同类: 是**启动期**错误
    (命令敲错了 / 环境没配好), 两个入口都把它翻成一行人话, 不打印 traceback。
    """


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """客服服务进程怎么跑 (监听地址 / 端口 / 校验令牌).

    attributes:
        host: 监听地址。
        port: 监听端口。
        token: 校验 Django 转发件用的内部令牌 (与商城侧同值)。**一个进程一个**:
            同一份值既用来认出「转发方是自己人」, 也被客户端拿去打商城 —— 同一
            信任域里的同一个口令 (PRD §4.10)。`repr=False`: 共享秘密不该跟着
            对象被打印进 traceback 或日志。
    """

    host: str
    port: int
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ContextConfig:
    """上下文压缩的五个旋钮 (环境变量 → 一份只读配置).

    它是**纯数据** (与 `ServerConfig` 同一个形状): 「读 env」归本模块, 「翻译成
    框架的零件」归装配处 (`service.build_compaction_for`) —— 两边各自的失败方式
    因此不会混在一起 (读错了是配置错, 翻错了是接错线).

    attributes:
        max_tokens: 单请求上下文预算: 估算到这里就压缩 (压完要落到水位线以下).
        keep_turns: 最近几轮**完整**保留. 这里的「一轮」是**一次问答的全过程**
            (一个提问连同为它做的全部工具往返), 别与 `service.max_turns` 那个
            「轮」混 —— 那个是**一次运行内**最多几次模型决策, 两个量不同级
            (CONTEXT.md 把前者叫「运行」). 刀口只落在提问处, 于是留下的永远是
            完整对话 (agent/compaction.py 的硬不变量).
        tool_limit: 更老的工具结果正文截到这个字符数 (正在回答的那一轮不截).
        summary: 摘要开关. 关掉 = 只裁剪 + 截断, 压缩这一步不再发模型调用.
        watermark: 水位线: 压到 `max_tokens` 的这个倍数以下才停手 (滞回: 只按下限
            压的话, 下一次请求立刻又超线, 于是每次都压、每次都花钱).
    """

    max_tokens: int = DEFAULT_CONTEXT_MAX_TOKENS
    keep_turns: int = DEFAULT_CONTEXT_KEEP_TURNS
    tool_limit: int = DEFAULT_CONTEXT_TOOL_LIMIT
    summary: bool = DEFAULT_CONTEXT_SUMMARY
    watermark: float = DEFAULT_CONTEXT_WATERMARK


def token_from_env(env: Mapping[str, str] | None = None) -> str:
    """读内部令牌 (**唯一一处**); 空着就抛。

    两个方向读的是同一个值: 客户端拿它打商城, 服务端拿它认转发件。

    Raises:
        MinimallConfigError: `CHARAPP_INTERNAL_TOKEN` 没配。这一条**不兜底**:
            令牌空着时商城会拒掉每一个请求 (服务端也该一律拒绝), 与其让使用者
            对着「内部错误」猜, 不如当场说清该去哪儿配。
    """
    values = os.environ if env is None else env
    token = (values.get(ENV_TOKEN) or "").strip()
    if not token:
        raise MinimallConfigError(
            f"{ENV_TOKEN} 未配置 —— 助手调不动商城的内部端点 (那边没有令牌就全拒)。"
            f"请在仓库根 .env 里填上与商城同值的令牌, 模板见 .env.example"
        )
    return token


def client_from_env(env: Mapping[str, str] | None = None) -> MinimallClient:
    """按环境变量建一个商城客户端 (一个进程一个, 退出时 `aclose()`)。

    Args:
        env: 环境变量映射; None 表示读 `os.environ` (测试传一个字典来钉死配置)。

    Returns:
        MinimallClient: 已装好地址与令牌的客户端 (身份不在这里 —— 每个方法自己收)。

    Raises:
        MinimallConfigError: `CHARAPP_INTERNAL_TOKEN` 没配 (见 `token_from_env`)。
    """
    values = os.environ if env is None else env
    base_url = (values.get(ENV_BASE_URL) or "").strip() or DEFAULT_BASE_URL
    return MinimallClient(base_url=base_url, token=token_from_env(values))


def thinking_from_env(env: Mapping[str, str] | None = None) -> bool | None:
    """读思考模式开关 (**服务端专用**); 不填 = 不传该参数 (走上游默认: 开启).

    三态而不是两态, 与框架那条契约对齐 (见 `model/protocol.py` 的 `thinking`):
    None = 不传 (上游默认开启) / True = 显式开 / False = 关. 空值因此**不等于**
    False —— 这也是「没配就与从前逐字一样」的原因.

    Args:
        env: 环境变量映射; None 表示读 `os.environ`。

    Returns:
        bool | None: 关 / 开 / 不传 (三态)。

    Raises:
        MinimallConfigError: 值不认识。这个开关直接决定每次请求的 token 与延迟,
            写错了该当场说清, 而不是猜一个方向.
    """
    values = os.environ if env is None else env
    raw = (values.get(ENV_THINKING) or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise MinimallConfigError(
        f"{ENV_THINKING} 只认 1/true/yes/on 或 0/false/no/off "
        f"(不填 = 上游默认开启), 实际: {raw!r}"
    )


def stream_from_env(env: Mapping[str, str] | None = None) -> bool:
    """读增量渲染开关 (**服务端专用**); **不填 = 开**.

    两态 (不像 `thinking_from_env` 那样有三态): 「不填」在这里没有第三种含义 ——
    它就是这个行为该有的默认值 (on). 想关掉 (对照实验 / 排障) 就显式写
    0/false/no/off.

    Args:
        env: 环境变量映射; None 表示读 `os.environ`.

    Returns:
        bool: 开 / 关.

    Raises:
        MinimallConfigError: 值不认识. 它决定用户看不看得见"逐字", 写错了该当场
            说清, 而不是猜一个方向.
    """
    values = os.environ if env is None else env
    return _bool_from_env(values, ENV_STREAM, True, note="开")


def _context_int(values: Mapping[str, str], name: str, default: int) -> int:
    """读一个整数旋钮: 空值 = 用默认; 不是整数、或不是一个正数就当场报."""
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise MinimallConfigError(f"{name} 不是整数: {raw!r}") from None
    if number < 1:
        raise MinimallConfigError(f"{name} 必须 >= 1, 实际: {number}")
    return number


def _context_float(values: Mapping[str, str], name: str, default: float) -> float:
    """读一个小数旋钮 (区间由调用方判: 每个旋钮的合法区间不一样)."""
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise MinimallConfigError(f"{name} 不是数字: {raw!r}") from None


def _bool_from_env(
    values: Mapping[str, str], name: str, default: bool, *, note: str = "用默认值"
) -> bool:
    """读一个开关: 空值 = 默认, truthy / falsy 各一组, 认不出就报.

    三处开关共用这一份解析 (`CHARAPP_CONTEXT_*` 的五个旋钮 / `CHARAPP_STREAM` /
    `CHARAPP_THINKING` 的三态在外层另写 —— 它的"空值"是 None 不是默认值, 见那个
    函数). `note` 只进错误消息的括注: 每个开关"不填"的含义不一样, 报错时要说清
    是哪一个 (不然使用者得自己猜"默认"指什么).
    """
    raw = (values.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise MinimallConfigError(
        f"{name} 只认 1/true/yes/on 或 0/false/no/off (不填 = {note}), 实际: {raw!r}"
    )


def _context_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    """上下文那五个旋钮里的开关 (空值 = 用默认值)."""
    return _bool_from_env(values, name, default)


def context_config_from_env(env: Mapping[str, str] | None = None) -> ContextConfig:
    """读上下文压缩的五个旋钮 (**五个都有默认值, 不填也能跑**).

    与 `thinking_from_env` 的空值语义一致: 不填 = 用默认 (不是「关掉」). 有区别的
    是这里的**每个值都当场校验**: 越界的配置在启动期就报, 而不是交给框架去发现
    —— 框架抛的 `CompactionConfigError` 不在 `STARTUP_ERRORS` 那张表里, 那意味着
    traceback 糊一屏, 而使用者要的是一句能照着改的中文话. 校验规则与框架那边
    (`TrimAndSummarize.__post_init__`) 是同一套, 所以正常的配置不会在这两处打架.

    Args:
        env: 环境变量映射; None 表示读 `os.environ`。

    Returns:
        ContextConfig: 五个旋钮 (缺的都填了默认值)。

    Raises:
        MinimallConfigError: 某个值不是数字 / 不是正数, 或水位线不在 0 与 1 之间
            (**报错信息里带变量名与实际值** —— 使用者手上只有 `.env` 那一行)。
    """
    values = os.environ if env is None else env
    watermark = _context_float(values, ENV_CONTEXT_WATERMARK, DEFAULT_CONTEXT_WATERMARK)
    if not 0 < watermark < 1:
        raise MinimallConfigError(
            f"{ENV_CONTEXT_WATERMARK} 必须在 0 与 1 之间 (它是阈值的比例), "
            f"实际: {watermark!r}"
        )
    return ContextConfig(
        max_tokens=_context_int(
            values, ENV_CONTEXT_MAX_TOKENS, DEFAULT_CONTEXT_MAX_TOKENS
        ),
        keep_turns=_context_int(
            values, ENV_CONTEXT_KEEP_TURNS, DEFAULT_CONTEXT_KEEP_TURNS
        ),
        tool_limit=_context_int(
            values, ENV_CONTEXT_TOOL_LIMIT, DEFAULT_CONTEXT_TOOL_LIMIT
        ),
        summary=_context_bool(values, ENV_CONTEXT_SUMMARY, DEFAULT_CONTEXT_SUMMARY),
        watermark=watermark,
    )


def server_config_from_env(env: Mapping[str, str] | None = None) -> ServerConfig:
    """按环境变量读服务进程的监听地址与端口; 没配就用默认值 (本机 1007)。

    Args:
        env: 环境变量映射; None 表示读 `os.environ`。

    Returns:
        ServerConfig: 监听地址 + 端口 + 校验令牌。

    Raises:
        MinimallConfigError: 令牌没配, 或端口不是 1~65535 的整数 (当场说清,
            而不是让 uvicorn 抛一句听不懂的)。
    """
    values = os.environ if env is None else env
    raw_port = (values.get(ENV_SERVER_PORT) or "").strip()
    port = DEFAULT_SERVER_PORT
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise MinimallConfigError(
                f"{ENV_SERVER_PORT} 不是整数: {raw_port!r}"
            ) from exc
        if not 1 <= port <= 65535:
            raise MinimallConfigError(f"{ENV_SERVER_PORT} 超出 1~65535: {port}")
    return ServerConfig(
        host=(values.get(ENV_SERVER_HOST) or "").strip() or DEFAULT_SERVER_HOST,
        port=port,
        token=token_from_env(values),
    )


# 知识库那条线 (L5) 的变量: 向量库地址 / 本地模型的根目录与名字 / 检索的两个 K /
# rewrite 开关. 与 charplot 的 CHARPLOT_* 是同一批概念, 命名跟这边的前缀走 ——
# 两个子项目可能同时活着, 变量名撞在一起时谁配谁不配就成了玄学.
ENV_MILVUS_URL = "CHARAPP_MILVUS_URL"
ENV_MODELSCOPE_ROOT = "CHARAPP_MODELSCOPE_ROOT"
ENV_EMBEDDING_MODEL = "CHARAPP_EMBEDDING_MODEL"
ENV_EMBEDDING_MODEL_NAME = "CHARAPP_EMBEDDING_MODEL_NAME"
ENV_EMBEDDING_DEVICE = "CHARAPP_EMBEDDING_DEVICE"
ENV_EMBEDDING_FP16 = "CHARAPP_EMBEDDING_FP16"
ENV_EMBEDDING_DIM = "CHARAPP_EMBEDDING_DIM"
ENV_RERANKER_MODEL = "CHARAPP_RERANKER_MODEL"
ENV_RERANKER_DEVICE = "CHARAPP_RERANKER_DEVICE"
ENV_RERANKER_FP16 = "CHARAPP_RERANKER_FP16"
ENV_RETRIEVE_TOP_K = "CHARAPP_RETRIEVE_TOP_K"
ENV_RERANK_TOP_K = "CHARAPP_RERANK_TOP_K"
ENV_QUERY_REWRITE = "CHARAPP_QUERY_REWRITE"

# 知识库那条线的默认值.
#
# **默认路径指到本机 modelscope 的下载目录** (与 charplot 那份同一个根): 本地模型是
# 这个仓库的演示前提之一, 而它的默认位置跟着下载工具的平铺布局走. 换机器就在 .env
# 里改 CHARAPP_MODELSCOPE_ROOT —— 模型缺失时 embedding 报错并给出下载命令, 不会
# 静默从 HuggingFace 拉一份 (~2GB, 且下载是主动行为).
DEFAULT_MILVUS_URL = "http://localhost:19530"
DEFAULT_MODELSCOPE_ROOT = r"D:/__WorkSpace__/modelscope"
DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
DEFAULT_EMBEDDING_DEVICE = "cpu"
DEFAULT_EMBEDDING_FP16 = False
DEFAULT_EMBEDDING_DIM = 1024
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_RERANKER_DEVICE = "cpu"
DEFAULT_RERANKER_FP16 = False

# 检索的两个 K: 混合召回量 (精排前的候选) 与精排后真正回给模型的片段数.
#
# **这两个数是被当前语料的规模钉住的** (2026-10-04 改判): 现在库里是 6 篇文章、
# 7 条 chunk, 召回 5 条就已经覆盖了大半个库 —— 再往上召回的是"同一篇里的另一段",
# 而精排在 CPU 上是**逐对**前向 (实测 bge-reranker-v2-m3 约 1.1 秒/对, 8 线程),
# 多一条候选就多一秒多的等待, 换来的排序质量在这么小的库上看不出差别.
#
# 精排后给 3 条: 一个政策问题通常落在一两段里, 3 段够答且不白烧 token —— 库里
# 一共 7 条, 给 5 条等于把半个库塞进上下文.
#
# **语料涨上去时这两个数要一起往上调** (几百篇文档时召回 5 条就太窄了), 而那一
# 天该重新看的还有 rerank 本身的延迟 (逐对前向是线性成本).
DEFAULT_RETRIEVE_TOP_K = 5
DEFAULT_RERANK_TOP_K = 3

# Query rewriting 开关: 关掉 = 检索直接用原查询 (省一次模型调用, 也去掉一个
# 失败点); 开着时改写失败自动降级原查询 (见 knowledge/query_rewrite.py).
DEFAULT_QUERY_REWRITE = True


@dataclass(frozen=True, slots=True)
class KnowledgeConfig:
    """知识库那条线的配置 (环境变量 → 一份只读配置).

    与 `ContextConfig` 同一个形状: 它是**纯数据** —— 读 env 归 `config.py`, 把这
    些值翻成模型 / 向量库零件归 `knowledge/` 包, 两边的失败方式因此不会混在一起.

    attributes:
        milvus_url: Milvus 地址 (与别的子项目共用实例也没关系: collection 名是
            本业务专用的 `ca_knowledge`).
        modelscope_root: 本地模型的根目录 (modelscope 平铺布局 `{root}/models/
            {org}/{name}`); `resolve_local_model_path` 按它解析 `org/name` 引用.
        embedding_model: Embedder 注册名 (工厂的键, 默认 `bge-m3`).
        embedding_model_name: 模型的本地引用 —— 一个已存在的目录, 或 `BAAI/bge-m3`
            这样的 `org/name` (按 modelscope_root 解析). **不会触发自动下载**:
            解析不到就报错, 报错里带着可照抄的下载命令.
        embedding_device: `cpu` / `cuda:0` (与 torch 的说法一致).
        embedding_fp16: 半精度开关. CPU 上一般关着 (bge-m3 在 CPU 上 fp16 反而更慢).
        embedding_dim: 稠密向量维度 (bge-m3 = 1024); 建 collection 时用它, 与
            embedding 模型必须对得上 —— 换模型要重建索引.
        reranker_model: 精排模型引用 (同样的解析规则); **缺失时降级不精排**
            (warning 明示), 不像 embedding 那样报错 —— 精排是增强, 不是链路前提.
        reranker_device: 精排模型设备.
        reranker_fp16: 精排半精度开关.
        retrieve_top_k: 混合检索召回几条候选 (精排前).
        rerank_top_k: 精排后交回几条片段.
        query_rewrite: 检索前是否用 LLM 改写查询 (写进知识库那条链路的唯一开关).
    """

    milvus_url: str = DEFAULT_MILVUS_URL
    modelscope_root: str = DEFAULT_MODELSCOPE_ROOT
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_model_name: str = DEFAULT_EMBEDDING_MODEL_NAME
    embedding_device: str = DEFAULT_EMBEDDING_DEVICE
    embedding_fp16: bool = DEFAULT_EMBEDDING_FP16
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    reranker_model: str = DEFAULT_RERANKER_MODEL
    reranker_device: str = DEFAULT_RERANKER_DEVICE
    reranker_fp16: bool = DEFAULT_RERANKER_FP16
    retrieve_top_k: int = DEFAULT_RETRIEVE_TOP_K
    rerank_top_k: int = DEFAULT_RERANK_TOP_K
    query_rewrite: bool = DEFAULT_QUERY_REWRITE

    def local_model_dir(self, model_ref: str) -> Path:
        """模型引用 → 它**该在**的本地目录 (不看存不存在).

        规则只有这一处 (与 charplot 那份同一套, 于是两边的 .env 写法可以互相照抄):

        1. 引用本身即路径 → 就是它
        2. `org/name` 风格引用 → `{modelscope_root}/models/{org}/{name}` 平铺布局

        不判断存在性是为了让**报错信息**能用同一个规则说"期望在哪儿" ——
        `resolve_local_model_path` 与缺失时那句下载命令都从本方法取路径, 布局
        变一次只改这里.
        """
        ref = Path(model_ref.strip())
        if len(ref.parts) == 2 and not Path(ref.parts[0]).is_absolute():
            return Path(self.modelscope_root) / "models" / ref.parts[0] / ref.parts[1]
        return ref

    def resolve_local_model_path(self, model_ref: str) -> str | None:
        """把模型引用解析为**已存在**的本地目录 (含 config.json), 否则 None.

        存在性由本方法判 (目录 + `config.json`); 路径怎么算见 `local_model_dir`.

        返回 None 表示本地没有 → 调用方据此报错 (embedding) 或降级 (rerank),
        **不触发任何库级自动下载**: 下模型是几百 MB 起步的主动行为, 不该藏在
        一次「启动服务」或「跑一次索引」里.
        """
        candidate = self.local_model_dir(model_ref)
        if candidate.is_dir() and (candidate / "config.json").is_file():
            return str(candidate)
        return None


def knowledge_config_from_env(env: Mapping[str, str] | None = None) -> KnowledgeConfig:
    """读知识库那条线的配置 (**每个值都有默认, 不填也能跑**).

    Args:
        env: 环境变量映射; None 表示读 `os.environ` (测试传一个字典来钉死配置).

    Returns:
        KnowledgeConfig: 一份具体的配置 (缺的填默认值).

    Raises:
        MinimallConfigError: 端口式的数字旋钮不是正整数, 或开关写了看不懂的值
            (与 `context_config_from_env` 同一条: 启动期就说清, 别等第一次检索).
    """
    values = os.environ if env is None else env
    return KnowledgeConfig(
        milvus_url=(values.get(ENV_MILVUS_URL) or "").strip() or DEFAULT_MILVUS_URL,
        modelscope_root=(values.get(ENV_MODELSCOPE_ROOT) or "").strip()
        or DEFAULT_MODELSCOPE_ROOT,
        embedding_model=(values.get(ENV_EMBEDDING_MODEL) or "").strip()
        or DEFAULT_EMBEDDING_MODEL,
        embedding_model_name=(values.get(ENV_EMBEDDING_MODEL_NAME) or "").strip()
        or DEFAULT_EMBEDDING_MODEL_NAME,
        embedding_device=(values.get(ENV_EMBEDDING_DEVICE) or "").strip()
        or DEFAULT_EMBEDDING_DEVICE,
        embedding_fp16=_context_bool(
            values, ENV_EMBEDDING_FP16, DEFAULT_EMBEDDING_FP16
        ),
        embedding_dim=_context_int(values, ENV_EMBEDDING_DIM, DEFAULT_EMBEDDING_DIM),
        reranker_model=(values.get(ENV_RERANKER_MODEL) or "").strip()
        or DEFAULT_RERANKER_MODEL,
        reranker_device=(values.get(ENV_RERANKER_DEVICE) or "").strip()
        or DEFAULT_RERANKER_DEVICE,
        reranker_fp16=_context_bool(values, ENV_RERANKER_FP16, DEFAULT_RERANKER_FP16),
        retrieve_top_k=_context_int(values, ENV_RETRIEVE_TOP_K, DEFAULT_RETRIEVE_TOP_K),
        rerank_top_k=_context_int(values, ENV_RERANK_TOP_K, DEFAULT_RERANK_TOP_K),
        query_rewrite=_context_bool(values, ENV_QUERY_REWRITE, DEFAULT_QUERY_REWRITE),
    )


def eval_payment_password(env: Mapping[str, str] | None = None) -> str | None:
    """读跑分模拟买家确认时要注入的那次支付密码 (issue 43); 没配给 None.

    为什么**不报错** (与 `token_from_env` 那条相反): 这个值只有代付那一类题用得上,
    而题集里绝大多数题连挂起都不会 —— 没配就拒绝整批跑分, 等于让一道题的决定拖住
    其余全部. 缺了它的后果是**看得见的**: 那一跑的终局是「模拟确认失败」(见
    `eval.subject`), 报告上单列一条, 而不是变成一个没人察觉的假成功.

    Args:
        env: 环境变量映射 (默认读 `os.environ`; 测试用它钉死配置).

    Returns:
        str | None: 密码原文; 没配 / 只写了空白 → None.
    """
    values = os.environ if env is None else env
    return (values.get(ENV_EVAL_PAYMENT_PASSWORD) or "").strip() or None


__all__ = [
    "DEFAULT_CONTEXT_KEEP_TURNS",
    "DEFAULT_CONTEXT_MAX_TOKENS",
    "DEFAULT_CONTEXT_SUMMARY",
    "DEFAULT_CONTEXT_TOOL_LIMIT",
    "DEFAULT_CONTEXT_WATERMARK",
    "DEFAULT_EMBEDDING_DEVICE",
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_FP16",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_MODEL_NAME",
    "DEFAULT_MILVUS_URL",
    "DEFAULT_MODELSCOPE_ROOT",
    "DEFAULT_QUERY_REWRITE",
    "DEFAULT_RERANKER_DEVICE",
    "DEFAULT_RERANKER_FP16",
    "DEFAULT_RERANKER_MODEL",
    "DEFAULT_RERANK_TOP_K",
    "DEFAULT_RETRIEVE_TOP_K",
    "DEFAULT_SERVER_HOST",
    "DEFAULT_SERVER_PORT",
    "ENV_BASE_URL",
    "ENV_CONTEXT_KEEP_TURNS",
    "ENV_CONTEXT_MAX_TOKENS",
    "ENV_CONTEXT_SUMMARY",
    "ENV_CONTEXT_TOOL_LIMIT",
    "ENV_CONTEXT_WATERMARK",
    "ENV_EMBEDDING_DEVICE",
    "ENV_EMBEDDING_DIM",
    "ENV_EMBEDDING_FP16",
    "ENV_EMBEDDING_MODEL",
    "ENV_EMBEDDING_MODEL_NAME",
    "ENV_EVAL_PAYMENT_PASSWORD",
    "ENV_MILVUS_URL",
    "ENV_MODELSCOPE_ROOT",
    "ENV_QUERY_REWRITE",
    "ENV_RERANKER_DEVICE",
    "ENV_RERANKER_FP16",
    "ENV_RERANKER_MODEL",
    "ENV_RERANK_TOP_K",
    "ENV_RETRIEVE_TOP_K",
    "ENV_SERVER_HOST",
    "ENV_SERVER_PORT",
    "ENV_THINKING",
    "ENV_TOKEN",
    "ContextConfig",
    "KnowledgeConfig",
    "MinimallConfigError",
    "ServerConfig",
    "client_from_env",
    "context_config_from_env",
    "eval_payment_password",
    "knowledge_config_from_env",
    "server_config_from_env",
    "thinking_from_env",
    "token_from_env",
]
