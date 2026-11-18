"""AlpacaBroker against a fake transport. No keys, no network."""

import pytest

from execution_core import OMS, OrderIntent, OrderState, OrderType, Side
from execution_core.adapters import AlpacaBroker
from execution_core.adapters.alpaca import LIVE_URL, PAPER_URL, AlpacaError


class FakeAlpaca:
    """Minimal in-memory stand-in for the Alpaca v2 endpoints the adapter uses."""

    def __init__(self):
        self.calls = []
        self.orders = {}          # client_order_id -> order json
        self.reject_duplicates = True

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, body))
        assert headers["APCA-API-KEY-ID"] == "k" and headers["APCA-API-SECRET-KEY"] == "s"
        path = url.split(".markets", 1)[1]
        if method == "GET" and path == "/v2/account":
            return 200, {"cash": "25000.50", "equity": "26000", "buying_power": "50000"}
        if method == "GET" and path == "/v2/positions":
            return 200, [{"symbol": "SPY", "qty": "3", "avg_entry_price": "580.25"}]
        if method == "GET" and path.startswith("/v2/orders:by_client_order_id"):
            key = path.split("client_order_id=", 1)[1]
            o = self.orders.get(key)
            return (200, o) if o else (404, {"message": "order not found"})
        if method == "POST" and path == "/v2/orders":
            key = body["client_order_id"]
            if key in self.orders and self.reject_duplicates:
                return 422, {"message": "client_order_id must be unique"}
            o = {"id": f"oid-{len(self.orders) + 1}", "client_order_id": key, "symbol": body["symbol"],
                 "qty": body["qty"], "side": body["side"], "type": body["type"],
                 "limit_price": body.get("limit_price"), "status": "accepted",
                 "filled_qty": "0", "filled_avg_price": None}
            self.orders[key] = o
            return 200, o
        if method == "DELETE" and path.startswith("/v2/orders/"):
            return 204, None
        return 500, {"message": f"unexpected {method} {path}"}


def _broker(fake=None, **kw):
    fake = fake or FakeAlpaca()
    return AlpacaBroker(api_key="k", secret_key="s", transport=fake, **kw), fake


def test_defaults_to_paper_and_constructs_without_keys():
    b = AlpacaBroker()
    assert b.paper is True and b.base_url == PAPER_URL
    assert AlpacaBroker(paper=False).base_url == LIVE_URL
    assert "paper" in b.capabilities().notes


def test_missing_keys_fail_loudly_on_call(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(AlpacaError):
        AlpacaBroker(transport=FakeAlpaca()).get_balances()


def test_reads_account_and_positions():
    b, _ = _broker()
    bal = b.get_balances()
    assert (bal.cash, bal.equity, bal.buying_power) == (25000.50, 26000.0, 50000.0)
    pos = b.get_positions()
    assert pos[0].symbol == "SPY" and pos[0].qty == 3 and pos[0].avg_price == 580.25


def test_limit_order_body_and_state():
    b, fake = _broker()
    ack = b.place_order(OrderIntent("SPY", Side.BUY, 2, OrderType.LIMIT, limit_price=581.0), "ck-1")
    assert ack.state == OrderState.SENT and ack.broker_order_id == "oid-1"
    post = [c for c in fake.calls if c[0] == "POST"][0][2]
    assert post == {"symbol": "SPY", "qty": "2", "side": "buy", "type": "limit",
                    "time_in_force": "day", "client_order_id": "ck-1", "limit_price": "581.0"}
    o = b.get_order("ck-1")
    assert o.intent.limit_price == 581.0 and o.intent.side == Side.BUY


def test_same_client_key_never_posts_twice():
    b, fake = _broker()
    b.place_order(OrderIntent("SPY", Side.BUY, 1), "dup")
    b.place_order(OrderIntent("SPY", Side.BUY, 1), "dup")
    assert sum(1 for c in fake.calls if c[0] == "POST") == 1


def test_duplicate_race_returns_original_order():
    fake = FakeAlpaca()
    b, _ = _broker(fake)
    b.place_order(OrderIntent("SPY", Side.BUY, 1), "race")
    real_get = b.get_order
    calls = {"n": 0}

    def stale_then_real(key):        # first lookup misses (race), second sees the order
        calls["n"] += 1
        return None if calls["n"] == 1 else real_get(key)

    b.get_order = stale_then_real
    ack = b.place_order(OrderIntent("SPY", Side.BUY, 1), "race")
    assert ack.broker_order_id == "oid-1"


def test_options_rejected_in_v1():
    b, _ = _broker()
    with pytest.raises(NotImplementedError):
        b.place_order(OrderIntent("NVDA_SPREAD", Side.BUY, 1, meta={"instrument": "option"}), "o1")


def test_http_errors_surface():
    fake = lambda m, u, h, body: (403, {"message": "forbidden"})
    b = AlpacaBroker(api_key="k", secret_key="s", transport=fake)
    with pytest.raises(AlpacaError) as e:
        b.get_balances()
    assert e.value.status == 403


def test_works_behind_the_oms():
    b, fake = _broker()
    oms = OMS(b)
    o1 = oms.place(OrderIntent("SPY", Side.BUY, 1), "oms-1")
    o2 = oms.place(OrderIntent("SPY", Side.BUY, 1), "oms-1")
    assert o1 is o2 and o1.broker_order_id == "oid-1"
    b.cancel("oid-1")
    assert fake.calls[-1][0] == "DELETE"
