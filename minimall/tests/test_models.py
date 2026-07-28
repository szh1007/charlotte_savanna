"""Model tests."""

from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase

from minimall.models import (
    Cart,
    CartItem,
    Category,
    Order,
    Product,
    ProductImage,
    Profile,
    ShippingAddress,
)

User = get_user_model()


class UserModelTest(TestCase):
    def test_create_user_with_profile(self):
        user = User.objects.create_user(username="u1", email="u1@t.com", password="pass")
        Profile.objects.create(user=user)
        profile = user.minimall_profile
        self.assertIsNotNone(profile)
        profile.set_payment_password("123456")
        self.assertTrue(profile.check_payment_password("123456"))
        self.assertFalse(profile.check_payment_password("wrong"))


class CategoryModelTest(TestCase):
    def test_tree_structure(self):
        root = Category.objects.create(name="A", slug="a")
        child = Category.objects.create(name="B", slug="b", parent=root)
        grandchild = Category.objects.create(name="C", slug="c", parent=child)
        self.assertEqual(list(root.get_descendants()), [child, grandchild])
        self.assertEqual(list(grandchild.get_ancestors()), [root, child])


class ProductModelTest(TestCase):
    def test_slug_auto_generation(self):
        cat = Category.objects.create(name="X", slug="x")
        p = Product.objects.create(name="My Product", category=cat, price=10.00)
        self.assertEqual(p.slug, "my-product")

    def test_product_images(self):
        cat = Category.objects.create(name="X", slug="img-test")
        p = Product.objects.create(name="P", category=cat, price=10.00)
        img1 = ProductImage.objects.create(product=p, image="products/test1.jpg", sort_order=0)
        ProductImage.objects.create(product=p, image="products/test2.jpg", sort_order=1)
        self.assertEqual(p.images.count(), 2)
        self.assertEqual(p.images.first(), img1)


class CartModelTest(TestCase):
    def test_unique_together(self):
        user = User.objects.create_user(username="cu", email="cu@t.com", password="pass")
        cat = Category.objects.create(name="X", slug="cart-test")
        prod = Product.objects.create(name="P", category=cat, price=10.00)
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, product=prod, quantity=1)
        with self.assertRaises(IntegrityError):
            CartItem.objects.create(cart=cart, product=prod, quantity=1)


class OrderModelTest(TestCase):
    def test_status_choices(self):
        self.assertEqual(Order.Status.PENDING, "pending")
        self.assertEqual(len(Order.Status.choices), 7)

    def test_order_no_unique(self):
        user = User.objects.create_user(username="ou", email="ou@t.com", password="pass")
        cat = Category.objects.create(name="X", slug="order-test")
        Product.objects.create(name="P", category=cat, price=10.00, stock=10)
        ShippingAddress.objects.create(
            user=user,
            receiver_name="A",
            phone="1",
            province="X",
            city="Y",
            district="Z",
            detail="D",
        )
        o1 = Order.objects.create(
            order_no="TEST001", user=user, total_amount=10.00, shipping_address_snapshot={}
        )
        self.assertEqual(str(o1), "TEST001")
        with self.assertRaises(IntegrityError):
            Order.objects.create(
                order_no="TEST001", user=user, total_amount=10.00, shipping_address_snapshot={}
            )
