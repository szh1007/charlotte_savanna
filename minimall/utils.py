"""工具函数."""

import random
from datetime import datetime


def generate_order_no(user_id: int) -> str:
    """生成订单号: YYYYMMDDHHMMSS + user_id + 4位随机数."""
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = "".join(str(random.randint(0, 9)) for _ in range(4))
    return f"{now}{user_id:06d}{suffix}"
