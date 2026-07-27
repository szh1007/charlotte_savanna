from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class Profile(models.Model):
    """买家扩展信息 - 与 auth_user 通过 OneToOne 关联.

    is_staff 区分管理员: auth_user.is_staff=True -> 管理员
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="minimall_profile",
        verbose_name="用户",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name="手机号",
    )
    avatar = models.ImageField(
        upload_to="avatars/",
        blank=True,
        null=True,
        verbose_name="头像",
    )
    payment_password = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        verbose_name="支付密码",
        help_text="6 位数字支付密码, 以哈希存储",
    )

    class Meta:
        db_table = "minimall_profile"
        verbose_name = "用户扩展"
        verbose_name_plural = verbose_name

    def set_payment_password(self, raw_password: str) -> None:
        self.payment_password = make_password(raw_password)

    def check_payment_password(self, raw_password: str) -> bool:
        if not self.payment_password:
            return False
        return check_password(raw_password, self.payment_password)
