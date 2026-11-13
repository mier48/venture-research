"""Comprobación de economía unitaria (5.3): hipótesis de trabajo, nunca criterio
eliminatorio."""

from __future__ import annotations

import math
from decimal import Decimal


def required_paying_customers(
    target_profit_eur_month: Decimal,
    price_eur_month: Decimal,
    variable_cost_eur_month: Decimal,
    fixed_cost_eur_month: Decimal,
) -> int | None:
    margin = price_eur_month - variable_cost_eur_month
    if margin <= 0:
        return None
    return math.ceil((target_profit_eur_month + fixed_cost_eur_month) / margin)
