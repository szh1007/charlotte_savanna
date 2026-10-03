"""演示数据准备: 把买家推到「可以开演」的状态, 且**可以反复跑**.

演示剧本的第一句是「我的订单到哪了」, 第二句是「帮我把这单付了」—— 于是开演前必须
有一笔**未付款**订单, 且余额够付它. 上一轮演示把单付掉之后, 下一轮就没有标的了;
靠人手去页面上下一单既慢又容易忘, 所以这一步交给命令.

做两件事, 各对应剧本里的一种「跑完一轮就没了」:

1. **保证有一笔未付款订单** (代付那一步的标的). 已经有就复用, 不新建.
2. **保证余额不低于下限** (默认 1000, 够付十次 99 元的演示单). 够就不动.

**为什么这么在意幂等**: `sh/charapp_demo.sh` 每次启动都会跑一次本命令 —— 演示脚本
不该有「先手动准备一下」这种前置动作. 两次执行的差别只写在输出里 (第一次补了,
第二次没补), 买家看得见的几个数 (余额 / 未付款订单号 / 库存) 一模一样.

**它不碰的东西**: 不建买家 (演示账号是人注册出来的, 命令只认 `--user-id`), 不改
密码, 不清历史订单 —— 历史订单正是「查的是真库」那个论据的素材.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.minimall.models import Order, Product, Profile, ShippingAddress
from app.minimall.services import OrderServiceError, add_to_cart, create_order

User = get_user_model()

# 余额下限 (够付十次 99 元的演示单; 不够就补到这个数)
DEFAULT_BALANCE_FLOOR = Decimal("1000")

# 买家连一个收货地址都没有时才建的占位地址: 字段值一眼能认出是演示数据,
# 不在买家的地址簿里冒充一条真地址
DEMO_ADDRESS_FIELDS = {
    "receiver_name": "演示收货人",
    "phone": "13800000000",
    "province": "演示省",
    "city": "演示市",
    "district": "演示区",
    "detail": "演示地址 (demo_prepare 建)",
}


class Command(BaseCommand):
    help = "准备演示数据: 保证买家有一笔未付款订单与足够余额 (幂等, 可重复执行)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            required=True,
            help="演示买家 (auth User) 的主键; 没有默认值 —— 别让它悄悄作用在别人身上",
        )
        parser.add_argument(
            "--balance-floor",
            type=Decimal,
            default=DEFAULT_BALANCE_FLOOR,
            help=f"余额下限, 不足则补到这个数 (默认 {DEFAULT_BALANCE_FLOOR})",
        )

    def handle(self, *args, **options):
        user = User.objects.filter(pk=options["user_id"]).first()
        if user is None:
            raise CommandError(
                f"买家 #{options['user_id']} 不存在 —— 演示账号得先注册出来, "
                f"本命令只补数据不建账号"
            )
        floor = options["balance_floor"]
        profile = self._profile_for(user, floor)
        self.stdout.write(
            f"[demo_prepare] 买家 {user.username} (#{user.id}), 余额下限 {floor:.2f}"
        )

        changes = []
        balance_before = profile.balance
        if self._ensure_balance(profile, floor) is not None:
            changes.append(f"余额 {balance_before:.2f} → {profile.balance:.2f}")
        order, created = self._ensure_pending_order(user)
        self.stdout.write(f"[demo_prepare] {self._order_line(order, created)}")
        if created:
            changes.append("新建 1 笔未付款订单")

        # 「当前」单独一行, 且一定是**动手之后**的值: 上面那条改动行说的是过程,
        # 演示前要照着念的数是这一条 (余额多少、账上还欠几笔)
        pending = Order.objects.filter(user=user, status=Order.Status.PENDING).count()
        self.stdout.write(
            f"[demo_prepare] 当前: 余额 {profile.balance:.2f}, 待付款 {pending} 笔"
        )

        if changes:
            self.stdout.write(f"[demo_prepare] 结论: 已补齐 ({', '.join(changes)})")
        else:
            self.stdout.write("[demo_prepare] 结论: 数据已就绪, 本轮没有改动")

    def _profile_for(self, user, floor: Decimal) -> Profile:
        """拿买家的 Profile; 没有就按余额下限建一个 (老账号 / 手工造的账号)."""
        profile, created = Profile.objects.get_or_create(
            user=user, defaults={"balance": floor}
        )
        if created:
            self.stdout.write(
                f"[demo_prepare] 买家原来没有 Profile, 已建 (余额 {floor:.2f})"
            )
        return profile

    def _ensure_balance(self, profile: Profile, floor: Decimal) -> Decimal | None:
        """余额不足就补到下限.

        Returns:
            Decimal | None: 补进去的金额; 本来就够则 None.
        """
        if profile.balance >= floor:
            return None
        with transaction.atomic():
            # 锁内再看一眼余额: 命令与演示可能同时在跑, 判据要取加锁后的那份
            locked = Profile.objects.select_for_update().get(pk=profile.pk)
            if locked.balance >= floor:
                profile.refresh_from_db()
                return None
            topup = floor - locked.balance
            locked.balance = floor
            locked.save(update_fields=["balance"])
        profile.refresh_from_db()
        return topup

    def _ensure_pending_order(self, user) -> tuple[Order, bool]:
        """保证有一笔未付款订单.

        Returns:
            tuple[Order, bool]: 订单与「是不是这次新建的」.

        Note:
            新建走的是**服务层那两个函数** (`add_to_cart` + `create_order`), 不手写
            插入: 扣库存 / 锁行 / 订单号撞车重试 / 地址快照全在那边, 抄一份出来
            等于把演示数据变成第二处真相.

            报的是**最新**的那笔 (2026-10-03 真机改的): 剧本里那句「帮我把最近的
            那笔待付款订单付了」, 模型照订单列表 (新单在前) 挑的就是它 —— 报同一笔,
            脚本输出里的订单号才对得上演示真正付掉的那一笔.
        """
        existing = (
            Order.objects.filter(user=user, status=Order.Status.PENDING)
            .order_by("-created_at", "-id")
            .first()
        )
        if existing is not None:
            return existing, False

        # 最便宜的在售商品: 演示要花多少钱是可预期的, 不该随商品表变化而跳
        product = (
            Product.objects.filter(is_active=True, stock__gte=1)
            .order_by("price", "id")
            .first()
        )
        if product is None:
            raise CommandError(
                "没有可下单的商品 (在售且有余量) —— 先在后台加一件上架商品再演示"
            )
        address = self._address_of(user)
        try:
            item, _ = add_to_cart(user, product_id=product.id, quantity=1)
            return create_order(user, [item.id], address.id), True
        except OrderServiceError as exc:
            raise CommandError(f"建演示订单失败: {exc}") from exc

    def _address_of(self, user) -> ShippingAddress:
        """买家的收货地址; 一个都没有时才建占位地址 (下单必须要地址)."""
        address = (
            ShippingAddress.objects.filter(user=user)
            .order_by("-is_default", "id")
            .first()
        )
        if address is not None:
            return address
        address = ShippingAddress.objects.create(user=user, **DEMO_ADDRESS_FIELDS)
        self.stdout.write(
            f"[demo_prepare] 买家原来没有收货地址, 已建占位地址 #{address.id}"
        )
        return address

    @staticmethod
    def _order_line(order: Order, created: bool) -> str:
        # 数量前那一下用乘号是屏幕上要的写法 (换字母 x 读着不像一张订单), 于是按
        # 仓库里的同款做法压掉这条歧义警告 (见 app/charplot/tests/ 下的 RUF003)
        items = ", ".join(
            f"{item.product_name} ×{item.quantity}"  # noqa: RUF001
            for item in order.items.all()
        )
        what = "本次新建" if created else "复用既有"
        amount = f"{order.total_amount:.2f}"
        detail = f"{amount}, {items}" if items else amount
        return f"未付款订单 {order.order_no} ({detail}) —— {what}"
