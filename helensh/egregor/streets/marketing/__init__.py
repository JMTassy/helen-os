"""HELEN OS — Marketing Street.

Second concrete instance of the Street Template.
Proves the factory works with a different charter.
"""
from helensh.egregor.streets.marketing.street import (
    MARKETING_CHARTER,
    MARKETING_SHOPS,
    create_marketing_street,
)

__all__ = ["MARKETING_CHARTER", "MARKETING_SHOPS", "create_marketing_street"]
