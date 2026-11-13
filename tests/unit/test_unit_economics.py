from decimal import Decimal

from bore.domain.unit_economics import required_paying_customers


def test_margin_positive():
    assert (
        required_paying_customers(Decimal("500"), Decimal("30"), Decimal("5"), Decimal("100")) == 24
    )


def test_zero_or_negative_margin():
    assert (
        required_paying_customers(Decimal("500"), Decimal("5"), Decimal("5"), Decimal("0")) is None
    )
    assert (
        required_paying_customers(Decimal("500"), Decimal("4"), Decimal("5"), Decimal("0")) is None
    )
