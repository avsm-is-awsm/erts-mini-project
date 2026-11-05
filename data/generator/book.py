"""Price-time-priority limit order book for one product (the simulated exchange).

Emits feed messages as tuples (type, side, local_oid, price, qty) that the simulation
stamps with time and product.  Aggressive orders produce one 'E' per resting order hit;
a remainder rests with an 'A' (limit orders) or is dropped (IOC).  Cancels -> 'X',
partial cancels -> 'M'.
"""
from __future__ import annotations

import bisect
from collections import deque

BID, ASK = 0, 1


class Book:
    __slots__ = ("bids", "asks", "bid_px", "ask_px", "orders", "next_oid", "px_min", "px_max")

    def __init__(self, px_min: int = -999_900, px_max: int = 999_900):
        self.bids: dict[int, deque] = {}   # price -> FIFO of oids
        self.asks: dict[int, deque] = {}
        self.bid_px: list[int] = []        # ascending
        self.ask_px: list[int] = []        # ascending
        self.orders: dict[int, list] = {}  # oid -> [side, price, qty, owner, t_created]
        self.next_oid = 1
        self.px_min, self.px_max = px_min, px_max

    # ------------------------------------------------------------------ queries
    def best_bid(self) -> int | None:
        return self.bid_px[-1] if self.bid_px else None

    def best_ask(self) -> int | None:
        return self.ask_px[0] if self.ask_px else None

    def level_qty(self, side: int, px: int) -> int:
        q = (self.bids if side == BID else self.asks).get(px)
        return sum(self.orders[o][2] for o in q) if q else 0

    def owner_orders(self, owner) -> list[int]:
        return [oid for oid, o in self.orders.items() if o[3] == owner]

    # ------------------------------------------------------------------ internals
    def _rest(self, side: int, px: int, oid: int) -> None:
        levels, prices = (self.bids, self.bid_px) if side == BID else (self.asks, self.ask_px)
        q = levels.get(px)
        if q is None:
            levels[px] = q = deque()
            bisect.insort(prices, px)
        q.append(oid)

    def _unrest(self, side: int, px: int, oid: int) -> None:
        levels, prices = (self.bids, self.bid_px) if side == BID else (self.asks, self.ask_px)
        q = levels[px]
        q.remove(oid)
        if not q:
            del levels[px]
            prices.pop(bisect.bisect_left(prices, px))

    # ------------------------------------------------------------------ actions
    def submit(self, owner, side: int, px: int, qty: int, ioc: bool, now: int, out: list) -> int:
        """Limit (or IOC) order.  Appends messages to out; returns resting oid or 0."""
        px = max(self.px_min, min(self.px_max, px))
        if qty <= 0:
            return 0
        if side == BID:
            while qty > 0 and self.ask_px and self.ask_px[0] <= px:
                lvl = self.ask_px[0]
                fifo = self.asks[lvl]
                oid = fifo[0]
                o = self.orders[oid]
                q = min(qty, o[2])
                out.append(("E", ASK, oid, lvl, q, o[3]))
                o[2] -= q
                qty -= q
                if o[2] == 0:
                    fifo.popleft()
                    del self.orders[oid]
                    if not fifo:
                        del self.asks[lvl]
                        self.ask_px.pop(0)
        else:
            while qty > 0 and self.bid_px and self.bid_px[-1] >= px:
                lvl = self.bid_px[-1]
                fifo = self.bids[lvl]
                oid = fifo[0]
                o = self.orders[oid]
                q = min(qty, o[2])
                out.append(("E", BID, oid, lvl, q, o[3]))
                o[2] -= q
                qty -= q
                if o[2] == 0:
                    fifo.popleft()
                    del self.orders[oid]
                    if not fifo:
                        del self.bids[lvl]
                        self.bid_px.pop()
        if qty > 0 and not ioc:
            oid = self.next_oid
            self.next_oid += 1
            self.orders[oid] = [side, px, qty, owner, now]
            self._rest(side, px, oid)
            out.append(("A", side, oid, px, qty, owner))
            return oid
        return 0

    def cancel(self, oid: int, out: list) -> bool:
        o = self.orders.pop(oid, None)
        if o is None:
            return False
        self._unrest(o[0], o[1], oid)
        out.append(("X", o[0], oid, 0, 0, o[3]))
        return True

    def reduce(self, oid: int, by: int, out: list) -> bool:
        o = self.orders.get(oid)
        if o is None or by <= 0:
            return False
        if by >= o[2]:
            return self.cancel(oid, out)
        o[2] -= by
        out.append(("M", o[0], oid, 0, by, o[3]))
        return True

    def purge(self, out: list) -> None:
        """Gate closure: the exchange removes every remaining order (one 'X' each, oid order)."""
        for oid in sorted(self.orders):
            o = self.orders.pop(oid)
            out.append(("X", o[0], oid, 0, 0, o[3]))
        self.bids.clear()
        self.asks.clear()
        self.bid_px.clear()
        self.ask_px.clear()
