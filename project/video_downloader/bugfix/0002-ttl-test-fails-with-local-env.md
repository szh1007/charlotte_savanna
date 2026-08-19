# Bugfix 0002 — TTL 清理测试在本地 .env 配置下失败

> 记录日期: 2026-08-19 | 状态: 已修复 | 涉及文件: `tests/test_ttl_cleanup.py`

## 问题现象

`pytest tests/` 中 `test_cleanup_not_touch_fresh_task` 持续失败：

```
assert cleanup() == []
E assert [1] == []
```

即未过期的 fresh 任务（仅推进 1h）被 TTL 清理器删除了。其余 TTL 相关测试均通过，仅此一个失败。

## 原因追溯

通过注入时钟打印实际判定数值，发现：

```
completed_at: 1787152847.77
ttl: 60.0          ← 预期 86400 (24h)
age: 3600.000024   ← 1h
cleanup result: [1]
```

**`ttl` 不是默认的 86400，而是 60.0** —— 项目 `.env` 中配置了演示用短 TTL：

```
FREE_DELIVERY_TTL=60
MEMBER_DELIVERY_TTL=120
```

而 `test_cleanup_not_touch_fresh_task` 硬编码推进 `+3600` 秒并注释「1h << 24h TTL」，隐含假设 TTL 为默认 24h。在 60s TTL 的本地配置下，1h 早已远超有效期 → 任务被清理 → 断言失败。

其他 TTL 测试（`test_free_task_expired_after_24h` 等）使用 `clock["now"] += config.FREE_DELIVERY_TTL + 1` 相对配置值推进，天然兼容任意 TTL 配置，因此不受影响。

**结论**：这不是 cleaner 实现的 bug，是**测试对运行环境配置的隐式耦合**——测试前提（默认 TTL 24h/72h）被本地 .env 破坏，且失败与否取决于机器上的 .env 内容，其他环境（CI、无 .env）下行为不一致，属于脆弱测试。

## 如何修复

在 `tests/test_ttl_cleanup.py` 新增 autouse fixture，显式固定 TTL 为默认契约值，测试与运行环境解耦：

```python
@pytest.fixture(autouse=True)
def default_ttl(monkeypatch):
    """固定 TTL 为默认契约值 (24h/72h): 断言不依赖运行环境配置.

    本地 .env 的演示配置 (60s/120s) 会破坏本模块的过期判定假设
    (如 1h << 24h 的未过期断言), 测试显式覆盖与运行环境解耦.
    """
    monkeypatch.setattr(config, "FREE_DELIVERY_TTL", 24 * 3600)
    monkeypatch.setattr(config, "MEMBER_DELIVERY_TTL", 72 * 3600)
```

要点：

- `cleaner._delivery_ttl()` 运行时读取 `config.*` 属性，patch 立即生效
- autouse 覆盖本模块全部测试：相对推进（`config.FREE_DELIVERY_TTL + 1`）与硬编码推进（`+3600`）断言均基于确定性的 24h/72h
- 未改动测试断言本身——测试语义（默认契约 24h/72h）就是意图，问题仅在于前提未显式声明

**验证**：全量测试 59 通过（此前 58 通过 / 1 失败）。

## 替换方案（如需大变动时）

| 方案 | 说明 | 评价 |
|------|------|------|
| 测试用相对推进 | 把 `+3600` 改为 `config.FREE_DELIVERY_TTL // 2` | 仍依赖 .env 具体值，60s 配置下 30s 未过期能过，但配置变 2s 时又失败；未根除环境耦合，仅缓解 |
| 移除本地 .env 短 TTL | 演示功能改走其他方式 | .env 的短 TTL 是本地演示过期效果的合法配置，不应为测试迁就运行配置 |
| 每次运行 pytest 前清 TTL 环境变量 | CI 脚本或 conftest 顶部 `del os.environ[...]` | 隐式依赖，破坏 `.env` 加载语义，不直观 |

> 当前方案（autouse 固定默认值）最简洁且消除全部不确定性，无继续演进的必要。
