"""量一次真实流式提问的 TTFT (首字时间) —— C17 补 C18 的欠账.

```bash
python scripts/measure_ttft.py            # 默认 5 题
python scripts/measure_ttft.py --n 3
```

**为什么评测跑批器量不了 TTFT**: 它不跑 `_12` 作答节点 —— 那一节点是流式的, 而评测要
的是可复现的检索指标. 所以这个数只能单独量 (README §3.3 也这么写着).

**口径**: 从**发起查询**到**第一个 `delta` 事件到达**. 用户看到的就是这个 —— 检索多快
不算数, 屏幕上出第一个字才算.

**做法**: 不起 HTTP 服务, 在进程内跑查询图 (`is_stream=True`). `answer_service` 会把
LLM 的增量推给 session 队列, 记下第一条到达的时刻即可 —— 少一层网络与 SSE 编解码,
量到的是链路本身.
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from unittest.mock import patch

from app.process.query.agent.main_graph import graph
from app.process.query.agent.state import create_query_default_state
from app.rag_eval.dataset import load_batch_eval_cases
from app.shared.utils.sse_utils import (
    SSEEvent,
    create_sse_queue,
    remove_sse_queue,
)

TIMEOUT_S = 180


def measure_once(question: str, index: int) -> dict:
    """跑一道题, 返回 `{question, ttft_s, total_s, ok}`."""
    session_id = f"ttft_probe_{index}"
    events = create_sse_queue(session_id)
    state = create_query_default_state(
        session_id=session_id,
        original_query=question,
        is_stream=True,
    )

    started = time.perf_counter()
    worker = threading.Thread(
        target=_invoke_with_history_isolated, args=(state,), daemon=True
    )
    worker.start()

    ttft = None
    outcome = "timeout"
    deadline = started + TIMEOUT_S
    while time.perf_counter() < deadline:
        try:
            event = events.get(timeout=5)
        except queue.Empty:
            if not worker.is_alive():
                break
            continue
        if event["event"] == SSEEvent.DELTA and ttft is None:
            ttft = time.perf_counter() - started
        if event["event"] in (SSEEvent.FINAL, SSEEvent.ERROR):
            outcome = event["event"]
            break

    total = time.perf_counter() - started
    worker.join(timeout=5)
    remove_sse_queue(session_id)
    return {
        "question": question,
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "total_s": round(total, 3),
        "outcome": outcome,
    }


def _invoke_with_history_isolated(state) -> None:
    """跑查询图, 但把**历史会话读写**替换为空.

    本地 MongoDB 没起时, 主体识别那一步会去读历史 -> 整条图报错. 评测跑批器也是这么
    绕开的 (`runner.run_query_eval_case`), 口径一致. 补丁只碰历史那两下 (毫秒级),
    TTFT 关心的检索 + 作答全在补丁之外, 所以数字照可用.
    """
    with (
        patch(
            "app.rag.query.item_name_confirm_service._get_history_by_session_id",
            return_value=[],
        ),
        patch(
            "app.rag.query.item_name_confirm_service._save_user_chat_message",
            return_value=None,
        ),
    ):
        graph.invoke(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="流式提问的 TTFT (C17)")
    parser.add_argument("--n", type=int, default=5, help="量几道题")
    args = parser.parse_args()

    cases = [c for c in load_batch_eval_cases() if c["expected_item_names"]][: args.n]
    rows = []
    for i, case in enumerate(cases, start=1):
        result = measure_once(case["question"], i)
        rows.append(result)
        print(
            f"[{i}/{len(cases)}] TTFT={result['ttft_s']}s "
            f"总耗时={result['total_s']}s ({result['outcome']}) "
            f"{case['question'][:34]}"
        )

    values = sorted(r["ttft_s"] for r in rows if r["ttft_s"] is not None)
    if values:
        p50 = values[len(values) // 2]
        print(
            f"\nTTFT: n={len(values)} P50={p50:.2f}s 最快={values[0]:.2f}s "
            f"最慢={values[-1]:.2f}s"
        )
    else:
        print("\n一条都没量到 —— 检查 LLM 是否可用")


if __name__ == "__main__":
    main()
