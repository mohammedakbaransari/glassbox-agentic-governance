"""
GlassBox — Multi-Currency Support  (v1.0.0)
============================================
Normalises monetary amounts from any ISO 4217 currency to a base currency
(default USD) before policy evaluation, enabling global enterprise deployment.

Design:
  CurrencyNormalizer holds a configurable exchange-rate table. Rates default to
  approximate values that are updated via configure_rates(). In production, call
  configure_rates() on startup with live rates from your treasury system or a
  market data feed.

  The normalizer is stateless after configuration, thread-safe, and zero-dependency.

Usage:
    from glassbox.governance.currency import CurrencyNormalizer

    norm = CurrencyNormalizer()
    # Convert EUR 500,000 to USD equivalent
    usd_equiv = norm.to_base(500_000, "EUR")   # e.g. 540,000

    # Update with live rates
    norm.configure_rates({"EUR": 1.08, "GBP": 1.27, "JPY": 0.0067})

    # Auto-normalise a payload amount
    amount_usd = norm.normalise_payload_amount(payload, ctx)

Author: Mohammed Akbar Ansari — Independent Researcher
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional


# Default approximate rates relative to USD (1 USD = X units of foreign currency)
# These are approximations; update via configure_rates() for production use.
_DEFAULT_RATES_TO_USD: Dict[str, float] = {
    # Major currencies
    "USD": 1.0000,
    "EUR": 1.0850,    # 1 EUR = 1.0850 USD
    "GBP": 1.2700,    # 1 GBP = 1.2700 USD
    "JPY": 0.0067,    # 1 JPY = 0.0067 USD
    "CHF": 1.1200,    # 1 CHF = 1.1200 USD
    "CAD": 0.7350,    # 1 CAD = 0.7350 USD
    "AUD": 0.6500,    # 1 AUD = 0.6500 USD
    "NZD": 0.6000,    # 1 NZD = 0.6000 USD
    "CNY": 0.1390,    # 1 CNY = 0.1390 USD
    "HKD": 0.1280,    # 1 HKD = 0.1280 USD
    "SGD": 0.7500,    # 1 SGD = 0.7500 USD
    # South Asian
    "INR": 0.0120,    # 1 INR = 0.0120 USD
    "PKR": 0.0036,    # 1 PKR = 0.0036 USD
    "BDT": 0.0091,    # 1 BDT = 0.0091 USD
    "LKR": 0.0031,    # 1 LKR = 0.0031 USD
    # Middle East / Africa
    "AED": 0.2723,    # 1 AED = 0.2723 USD
    "SAR": 0.2667,    # 1 SAR = 0.2667 USD
    "QAR": 0.2747,    # 1 QAR = 0.2747 USD
    "ZAR": 0.0540,    # 1 ZAR = 0.0540 USD
    # Latin America
    "BRL": 0.2000,    # 1 BRL = 0.2000 USD
    "MXN": 0.0580,    # 1 MXN = 0.0580 USD
    "ARS": 0.0011,    # 1 ARS = 0.0011 USD
    # Other
    "KRW": 0.00075,   # 1 KRW = 0.00075 USD
    "SEK": 0.0960,    # 1 SEK = 0.0960 USD
    "NOK": 0.0940,    # 1 NOK = 0.0940 USD
    "DKK": 0.1450,    # 1 DKK = 0.1450 USD
    "PLN": 0.2500,    # 1 PLN = 0.2500 USD
    "CZK": 0.0440,    # 1 CZK = 0.0440 USD
    "HUF": 0.0028,    # 1 HUF = 0.0028 USD
}


class CurrencyNormalizer:
    """
    Thread-safe, zero-dependency multi-currency amount normaliser.

    All GlassBox financial policies operate in a base currency (default USD).
    CurrencyNormalizer converts amounts from any supported currency to USD
    before policy evaluation, enabling globally consistent limit enforcement.

    Example:
        norm = CurrencyNormalizer()
        # A EUR 600,000 procurement — is it above the $500K USD limit?
        usd = norm.to_base(600_000, "EUR")   # 651,000 — yes, needs contract_id
    """

    def __init__(self, base_currency: str = "USD"):
        self._base = base_currency.upper()
        self._rates: Dict[str, float] = dict(_DEFAULT_RATES_TO_USD)
        self._lock = threading.RLock()

    def configure_rates(self, rates: Dict[str, float]) -> None:
        """
        Update exchange rates. Call this on startup with live market rates.

        Args:
            rates: {currency_code: rate_to_USD} e.g. {"EUR": 1.085, "GBP": 1.27}
        """
        with self._lock:
            for k, v in rates.items():
                self._rates[k.upper()] = float(v)

    def to_base(self, amount: float, from_currency: str) -> float:
        """
        Convert amount from from_currency to base currency (USD).

        Returns amount unchanged if currency is unknown (safe fallback).
        """
        code = from_currency.upper()
        if code == self._base:
            return float(amount)
        with self._lock:
            rate = self._rates.get(code)
        if rate is None:
            return float(amount)   # unknown currency — pass through unchanged
        return float(amount) * rate

    def normalise_payload_amount(
        self,
        payload:  Dict[str, Any],
        currency: str = "USD",
    ) -> float:
        """
        Extract 'amount' from payload and normalise to base currency.
        Returns 0.0 if payload has no 'amount' field.
        """
        raw = float(payload.get("amount", 0))
        return self.to_base(raw, currency)

    def supported_currencies(self) -> list:
        with self._lock:
            return sorted(self._rates.keys())

    def rate(self, currency: str) -> Optional[float]:
        with self._lock:
            return self._rates.get(currency.upper())


# Module-level singleton — shared across all pipelines
_default_normalizer = CurrencyNormalizer()


def get_normalizer() -> CurrencyNormalizer:
    """Return the module-level CurrencyNormalizer instance."""
    return _default_normalizer


def configure_rates(rates: Dict[str, float]) -> None:
    """Configure exchange rates on the module-level normalizer."""
    _default_normalizer.configure_rates(rates)


def to_usd(amount: float, currency: str) -> float:
    """Convenience function: convert amount to USD using module-level normalizer."""
    return _default_normalizer.to_base(amount, currency)
