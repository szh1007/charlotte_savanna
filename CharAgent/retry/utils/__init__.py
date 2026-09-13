"""retry 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils, tool/utils, stream/utils, hooks/utils 惯例 —— 顶层放行为
模块, 静态支撑按主题收进子包:
- types.py   RetryAttempt (失败尝试记录) / RetryCallback (通知回调形态) /
             ClaimStatus (认领状态) / ClaimResult (认领裁决)
- errors.py  异常语义: RetryError 基类 + RetryConfigError (策略配置非法) +
             IdempotencyKeyError (幂等键非法)

模块内部 import 走具体模块路径 (retry.policy, retry.utils.types), 不绕包门面,
避免隐式循环依赖.

大白话版: 这里放 retry 包的「静态零件」(单据格式 / 错误定义), 不含任何行为
逻辑 —— 退避算法在 policy.py, 重试驱动在 executor.py, 模型接线在
chat_model.py, 幂等键与存储在 idempotency.py. 域内常量跟随其所属类型
(如幂等键的长度上限与字符集在 idempotency.py), 不上浮到这里.
"""

from __future__ import annotations
