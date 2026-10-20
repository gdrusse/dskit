# Statistical arbitrage

DSKit child for a structural equity network and option relative-value study.
Phase one uses AAPL, AMZN, and GOOGL as research/trading nodes and QQQ as
market factor and benchmark. MSFT/NVDA and the Energy universe are later,
independent additions by JSON. No historical P&L or model edge is claimed.

ADR-0270 in the parent decision log locks the current scope and approved layout.
The Path is in docs/decisioning. Source identities, universe policy, and a
zero-fit chronological calendar are in configs/. The Tier 3 modules and run
configs follow the generic DSKit mechanism contract. Training and validation
need a separately approved registration.
