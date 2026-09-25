"""日志脱敏: 写之前打码, 落盘后搜不到原文 (issue 29).

这个模块与 `test_redaction.py` 是**两件事** (与两个被测模块的分工一致):

- `test_redaction.py` 管**事件** (ADR-0003): 工具事件的载荷整条换成人话;
- 本文件管**日志**: 打码之后再写出去.

测三件事:

1. **验收那条**: 构造一条含手机号的日志**真写进文件**, 文件里搜不到原文 —— 用真
   文件而不是「函数返回了打码后的字符串」, 是因为这条链上任何一环接错 (名单漏了、
   writer 忘包了) 都要在这里红.
2. **名单按声明打**: 手机号 / 邮箱留头留尾, 余额 / 姓名 / 门牌整段抹掉, 而**订单号
   与商品名照旧** —— 打码不该把排查要用的东西一起打没.
3. **接的是生产那一条**: 那一行日志用的是框架**生产里那个**格式化函数拼出来的
   (不是这里抄一份格式), 过完出口之后原文不在了.

第 ① 个泄漏点 (关掉 uvicorn 访问日志) 的用例在 `test_server.py` —— 那里才是「这个
服务怎么跑」的落脚处 (`uvicorn_config`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from conftest import ADDRESSES, PROFILE

from CharAgent.client.app import _retry_notice
from CharAgent.retry.utils.types import RetryAttempt
from CharApp.minimall.log_redaction import (
    LOG_FIELDS,
    build_redactor,
    redacting_writer,
)
from CharApp.minimall.server import log_writer

# 商城样本里的手机号与订单号 (真形状, 见 `CharApp/tests/conftest.py`)
PHONE = "13800000003"
ORDER_NO = "202609191230450000031234"


def test_a_phone_number_never_reaches_the_log_file(tmp_path: Path) -> None:
    """验收: 构造一条含手机号的日志, **落盘后搜不到原文**.

    这一行不是手写的样本: 它由框架 `_retry_notice` (生产里拼重试提示的那个函数)
    按真实的格式拼出来 —— 上游模型报错的正文正是顺着这条路进日志的 (issue 29 的
    第三个泄漏点). 抄一份格式进测试就等于把它冻在这里, 以后格式变了测试还在验旧的.
    """
    log_file = tmp_path / "charagent.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    target = logging.getLogger("CharApp.minimall.server")
    target.addHandler(handler)
    level, target.level = target.level, logging.INFO
    try:
        # 供应商回的错误正文里带着手机号 (真机上就是这样流进来的)
        notice = _retry_notice(log_writer())
        notice(
            RetryAttempt(
                attempt=1,
                delay=0.4,
                elapsed=0.1,
                reason=f"ModelStatusError: 输入里有手机号 {PHONE}, 请检查",
            )
        )
    finally:
        target.removeHandler(handler)
        target.setLevel(level)
        handler.close()

    written = log_file.read_text(encoding="utf-8")

    assert written, "这一行没写进文件 (出口接错了)"
    assert PHONE not in written, "手机号原文落进了日志"
    assert "138****0003" in written, "打码后的那串该在日志里 (留头留尾看得见形状)"
    assert "重试" in written, "重试提示本身要留着 —— 打码不该把观测一起打没"


def test_the_declared_fields_are_masked_by_name_at_any_depth() -> None:
    """名单按字段名生效, 不管包了几层 (账户是平铺的, 地址是列表里的每一项)."""
    redactor = build_redactor()
    payload = {
        # 账户那一条的形状 (`app/minimall/serializers_agent.py` 的 profile)
        "phone": PHONE,
        "email": "buyer3@example.com",
        "balance": "9500.00",
        # 地址那一条: 列表里每一项
        "addresses": [
            {
                "receiver_name": "张三",
                "phone": PHONE,
                "detail": "文三路 100 号",
                "city": "杭州市",
            }
        ],
    }

    masked = redactor.redact_fields(payload)

    assert masked["phone"] == "138****0003"
    assert masked["email"] == "b*****@example.com"
    assert masked["balance"] == "[已抹除]"
    assert masked["addresses"][0] == {
        "receiver_name": "[已抹除]",
        "phone": "138****0003",
        "detail": "[已抹除]",
        "city": "杭州市",
    }, "该打的是那几个字段, 别的一格不动"


def test_the_order_number_and_product_names_survive() -> None:
    """订单号与商品名照旧 (打码只认那几类形状, 不猜本项目的编号).

    这条是「兜不住的如实留着」在业务侧的落点: 排查订单问题要看的正是订单号, 给它
    猜一条规则 (比如「一串数字就是敏感值」) 会把日志打成没法用.
    """
    redactor = build_redactor()
    line = f"订单 {ORDER_NO} 里的 红米 Note 13 已发货"

    assert redactor.redact_text(line) == line


def test_the_declared_names_are_the_ones_the_sample_payloads_carry() -> None:
    """名单里的名字与样本载荷对得上 (名字写错了, 名单就等于没有).

    样本 (`conftest` 的 PROFILE / ADDRESSES) 是照商城契约抄的, 所以这条是**两份独立
    抄件互相印证**: 名单里写错一个名字、或契约改了名而样本跟着改, 这里都会红 ——
    而这种错本身不会报错, 只会在某天日志里漏一次原文.

    `payment_password` 是唯一**不在**载荷里的一样 (ADR-0015 把它放进一次性载荷、
    永不进 wire), 所以它按「声明里得留着」校验, 不按载荷校验.
    """
    payload_names = set(PROFILE) | set(ADDRESSES[0])
    declared = {path.rsplit(".", 1)[-1] for path in LOG_FIELDS}

    assert declared - {"payment_password"} <= payload_names
    assert "payment_password" in declared, "支付密码那条提前声明要留着"


def test_the_writer_wrapper_only_changes_the_text() -> None:
    """包一层之后形状不变: 原来那个出口照样被调用一次, 只是拿到的字打了码."""
    seen: list[str] = []

    write = redacting_writer(seen.append, build_redactor())
    write(f"买家 {PHONE} 问了一句")

    assert seen == ["买家 138****0003 问了一句"]
