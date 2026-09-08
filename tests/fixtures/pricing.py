"""Pricing rules extracted from the 2011 rewrite."""
import math
from decimal import Decimal

VOLUME_DISCOUNT_THRESHOLD = 250
BASE_SHIPPING_FEE = 7.5


class PricingEngine:
    def __init__(self, tax_rate=0.19):
        self.tax_rate = tax_rate

    def total_price(self, subtotal, quantity):
        discount = self._volume_discount(subtotal, quantity)
        shipping = self.shipping_cost(subtotal)
        return (subtotal - discount) * (1 + self.tax_rate) + shipping

    def _volume_discount(self, subtotal, quantity):
        if subtotal > VOLUME_DISCOUNT_THRESHOLD and quantity >= 10:
            return subtotal * 0.12
        if quantity >= 5:
            return subtotal * 0.05
        return 0

    def shipping_cost(self, subtotal):
        if subtotal > 100:
            return 0
        return BASE_SHIPPING_FEE

    def _never_called(self):
        return "dead"
