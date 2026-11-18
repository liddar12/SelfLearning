# Brokers: capability matrix + recommendation

The app is **broker-agnostic by construction**: one `Broker` port
(`execution_core.broker.Broker`), many adapters. Swapping brokers changes an
adapter, never the OMS, risk gate, or self-learning spine. All specifics below
are `VERIFY` against each provider's current docs before wiring.

## Matrix

| Broker | Official API | Options | Streaming | Paper sandbox | Recommendation |
|---|---|---|---|---|---|
| **Schwab** | Yes | Yes | Yes (WS) | **No** (sim locally) | **Build first** — your account |
| **Alpaca** | Yes | Yes | Yes | Yes (real paper) | **Second** — proves the port; great for testing |
| Interactive Brokers | Yes | Yes (deep) | Yes | Yes | Later — powerful but heavy (gateway/TWS) |
| Tradier | Yes | Yes | Yes | Yes | Later — simple REST, options-friendly |
| **Robinhood** | **Agent connector only** (Agentic Trading MCP, May 27 2026, beta) | Stocks at launch | No | No | Optional ring-fenced live sleeve; not needed for v1 |

## Why Schwab first, then Alpaca

- **Schwab** is your brokerage account, so it's the real target. Its API is a
  retail REST + WebSocket surface with OAuth and rate limits — not a colocated
  venue, and with **no official paper sandbox**, so "paper" = `SimBroker`
  filling against live quotes.
- **Alpaca** is the cleanest way to *prove* broker-agnosticism: official API,
  options support, and a genuine paper endpoint. Implementing it second, with no
  changes to the core, is the test that the port is real.

## Robinhood (updated 2026-11-10)

This section previously said Robinhood has no official trading API. That changed on
**May 27, 2026**, when Robinhood launched **Agentic Trading** (beta): an official MCP
server (`agent.robinhood.com/mcp/trading`) that lets an AI agent trade a **separate,
ring-fenced agentic account** funded from your primary account, with an optional
per-trade approval toggle. It is still not a general REST trading API for this engine,
and reverse-engineered clients remain off-limits (ToS, stability, lockout risk).

Fit for this project: optional, as a capped live sleeve driven through MCP, after the
Alpaca paper record (S1) and Schwab approve-each (S2) are proven. `VERIFY` current
scope (instruments, approval defaults) before wiring. Sources: Finder review of
Robinhood Agentic Trading; StockBrokers.com agentic broker guide (Nov 2026).

## Proposed order of work (2026-11-10, pending your Gate 1 call; supersedes "Schwab first" above if accepted)

- **Alpaca paper first (S0/S1).** Real paper endpoint, so the port is tested end to
  end without money. `AlpacaBroker` is implemented (equities/ETFs) against fixtures.
- **Schwab live second (S2+).** Your account; no sandbox, so it starts at
  approve-each-order.

## Adding a broker (the whole job)

1. Implement `Broker` for the vendor in `execution_core/adapters/<name>.py`
   (`capabilities`, `get_positions`, `get_balances`, `place_order`, `cancel`,
   `get_order`), mapping the vendor API onto the shared domain types.
2. Keep `place_order` **idempotent on `client_key`**.
3. Isolate the network call behind an injectable seam; unit-test the parser
   against a fixture (the EIA/ISO pattern).
4. Nothing else changes — OMS, risk gate, TLH, and the spine are untouched.
