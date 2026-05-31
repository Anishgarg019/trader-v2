"""Zerodha equity charge model (spec §6.4.1). A backtest without realistic friction is
fiction.

Rates VERIFIED against https://zerodha.com/charges/ on 2026-05-31:

  EQUITY DELIVERY (CNC):
    brokerage          : ₹0 (zero)
    STT                : 0.1%  on buy & sell
    txn charges        : NSE 0.00307%, BSE 0.00375% (each leg)
    SEBI               : ₹10 / crore  (0.0001%)
    GST                : 18% on (brokerage + SEBI + txn charges)
    stamp duty         : 0.015% on BUY side
    DP charges         : ₹15.34 per scrip on SELL (delivery only)
    IPFT (NSE)         : negligible (~₹10/crore); modelled small & configurable

  EQUITY INTRADAY (MIS):
    brokerage          : 0.03% or ₹20 per executed order, whichever lower
    STT                : 0.025% on SELL side
    txn charges        : NSE 0.00307%, BSE 0.00375%
    SEBI / GST         : as above
    stamp duty         : 0.003% on BUY side
    DP charges         : none (no delivery)

Rates change — re-verify against zerodha.com/charges when in doubt (spec §6.4.1).
"""
from __future__ import annotations

from dataclasses import dataclass

CNC, MIS = "CNC", "MIS"
BUY, SELL = "BUY", "SELL"


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: float
    stt: float
    transaction: float
    sebi: float
    ipft: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.transaction + self.sebi
                + self.ipft + self.stamp + self.gst + self.dp)


@dataclass(frozen=True)
class CostModel:
    # transaction charges (fraction of turnover, per leg)
    txn_rate_nse: float = 0.0000307   # 0.00307%
    txn_rate_bse: float = 0.0000375   # 0.00375%
    sebi_rate: float = 0.000001       # ₹10/crore = 0.0001%
    ipft_rate: float = 0.000001       # NSE IPFT ~₹10/crore (small, configurable)
    gst_rate: float = 0.18            # on (brokerage + SEBI + txn)
    dp_charge: float = 15.34          # per scrip on delivery sells (incl GST)
    # STT
    stt_delivery: float = 0.001       # 0.1% buy & sell
    stt_intraday_sell: float = 0.00025  # 0.025% sell only
    # stamp duty (buy side)
    stamp_delivery_buy: float = 0.00015  # 0.015%
    stamp_intraday_buy: float = 0.00003  # 0.003%
    # intraday brokerage
    intraday_brokerage_rate: float = 0.0003  # 0.03%
    intraday_brokerage_cap: float = 20.0     # ₹20 per executed order

    def _txn_rate(self, exchange: str) -> float:
        return self.txn_rate_bse if exchange.upper() == "BSE" else self.txn_rate_nse

    def charge(self, transaction_type: str, product: str, exchange: str,
               qty: int, price: float) -> ChargeBreakdown:
        """Charges for ONE executed order leg (buy or sell)."""
        side = transaction_type.upper()
        prod = product.upper()
        turnover = float(qty) * float(price)

        if prod == CNC:
            brokerage = 0.0
            stt = self.stt_delivery * turnover                      # both sides
            stamp = self.stamp_delivery_buy * turnover if side == BUY else 0.0
            dp = self.dp_charge if side == SELL else 0.0
        elif prod == MIS:
            brokerage = min(self.intraday_brokerage_rate * turnover,
                            self.intraday_brokerage_cap)
            stt = self.stt_intraday_sell * turnover if side == SELL else 0.0
            stamp = self.stamp_intraday_buy * turnover if side == BUY else 0.0
            dp = 0.0
        else:
            raise ValueError(f"unsupported product {product!r} (use CNC or MIS)")

        transaction = self._txn_rate(exchange) * turnover
        sebi = self.sebi_rate * turnover
        ipft = self.ipft_rate * turnover
        gst = self.gst_rate * (brokerage + sebi + transaction)
        return ChargeBreakdown(brokerage, stt, transaction, sebi, ipft, stamp, gst, dp)

    def round_trip(self, product: str, exchange: str, qty: int,
                   entry_price: float, exit_price: float) -> float:
        """Total charges for a buy-then-sell round trip (convenience)."""
        buy = self.charge(BUY, product, exchange, qty, entry_price)
        sell = self.charge(SELL, product, exchange, qty, exit_price)
        return buy.total + sell.total
