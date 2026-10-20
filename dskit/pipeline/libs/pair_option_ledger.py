"""Atomic research pairing of two existing option-structure ledger outcomes.

This pack delegates quote selection, NBBO fills, option marks, expiry/exit cash, fees and
optional Black-76 delta hedge to :class:`OptionStructureLedger`. A pair appears in the
trade output only if both delegated orders open and close as requested. A refused or
unsettled side excludes both from realized performance, with a counted reason. Outputs
include *structure-level* cashflows; the delegated ledger does not expose individual
contract fill tickets. Settlement is its European cash approximation, with no financing.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping

from dskit.pipeline.records import number_ok, price_ok

from dskit.pipeline.libs.option_ledger import OptionStructureLedger
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params

__all__ = ["NODE_KINDS", "PairedOptionLedger"]
NODE_KINDS = ()


class PairedOptionLedger(Node):
    """Combine two delegated option orders into one atomic pair result.

    Parameters
    ----------
    params : dict
        ``ledger_params`` is a valid :class:`OptionStructureLedger` parameter block,
        excluding ``contracts``; fills must be NBBO. Pair keys default to ``id``,
        ``short_order``, ``long_order``, ``short_contracts`` and ``long_contracts``.
        An optional finite per-pair ``hedge_rate`` overrides the delegated hedge rate
        without modifying ``ledger_params``.
        Orders use the delegated ledger's ``order_fields`` names. Both orders need the
        same entry date, expiration and (when applicable) requested exit date.

    Examples
    --------
    Pair a short structure with a long structure::

        node = PairedOptionLedger("pair", {"ledger_params": ledger_config})
        out = node.run(ctx, {"pairs": [{"id": "p1", "short_order": short_order,
            "long_order": long_order, "short_contracts": 1,
            "long_contracts": 1}], "chain": chain_rows})
        # -> out["trades"] contains one result only if both legs close
    """

    role = "transform"
    outputs = ("trades", "marks", "cashflows", "refusals", "summary")
    _PARAMS = (
        "ledger_params",
        "pair_id_field",
        "short_order_field",
        "long_order_field",
        "short_contracts_field",
        "long_contracts_field",
        "hedge_rate_field",
    )
    _DEFAULT_FIELDS = {
        "pair_id_field": "id",
        "short_order_field": "short_order",
        "long_order_field": "long_order",
        "short_contracts_field": "short_contracts",
        "long_contracts_field": "long_contracts",
        "hedge_rate_field": "hedge_rate",
    }

    @classmethod
    def validate_params(cls, params):
        """Validate wrapper keys and delegate the underlying ledger contract.

        Parameters
        ----------
        params : dict
            Pair-ledger configuration.

        Returns
        -------
        list of str
            Configuration problems, empty when valid.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        inner = params.get("ledger_params")
        if not isinstance(inner, dict):
            problems.append("ledger_params must be a dict")
        else:
            if "contracts" in inner:
                problems.append("ledger_params.contracts is supplied by each pair")
            if inner.get("fill", "nbbo") != "nbbo":
                problems.append("ledger_params.fill must be nbbo")
            problems.extend(OptionStructureLedger.validate_params(inner))
        for name in cls._DEFAULT_FIELDS:
            if name in params and (
                not isinstance(params[name], str) or not params[name]
            ):
                problems.append(f"{name} must be a non-empty string")
        names = [params.get(name, value) for name, value in cls._DEFAULT_FIELDS.items()]
        if all(isinstance(name, str) for name in names) and len(set(names)) != len(
            names
        ):
            problems.append("pair field names must be distinct")
        return problems

    def run(self, ctx, inputs):
        """Replay both orders per pair and retain only matched closed results.

        Parameters
        ----------
        ctx : NodeContext
            Passed unchanged to the delegated ledgers.
        inputs : dict
            ``pairs`` is a list of pair orders; ``chain`` is a one-pass iterable of quote rows.

        Returns
        -------
        dict
            Closed pair ``trades``, combined daily ``marks``, structure-level
            ``cashflows``, counted ``refusals`` and ``summary``.

        Raises
        ------
        ValueError
            Input schema, pair identity or date alignment is invalid, or the delegated
            ledger reports malformed chain/contract data.
        """
        pairs = inputs.get("pairs")
        chain = inputs.get("chain")
        if not isinstance(pairs, (list, tuple)):
            raise ValueError(f"{self.key}: pairs must be a list or tuple")
        if chain is None or isinstance(chain, (str, bytes, Mapping)):
            raise ValueError(f"{self.key}: chain must be an iterable of rows")
        prepared = []
        trades, marks, cashflows, refusals = [], [], [], []
        reasons = Counter()
        ids = set()
        for index, pair in enumerate(pairs):
            pair_id, short_order, long_order, short_n, long_n, hedge_rate = self._pair(
                pair, index
            )
            if pair_id in ids:
                raise ValueError(f"{self.key}: duplicate pair id {pair_id!r}")
            ids.add(pair_id)
            self._aligned(pair_id, short_order, long_order)
            prepared.append(
                (pair_id, short_order, long_order, short_n, long_n, hedge_rate)
            )
        compact = self._compact_chain(chain, prepared) if prepared else None
        for pair_id, short_order, long_order, short_n, long_n, hedge_rate in prepared:
            pair_chain = self._pair_chain(compact, short_order, long_order)
            short, short_problem = self._leg(
                ctx, pair_chain, short_order, short_n, hedge_rate
            )
            long, long_problem = self._leg(
                ctx, pair_chain, long_order, long_n, hedge_rate
            )
            status = self._status(short, long, short_problem, long_problem)
            if status is not None:
                reasons[status] += 1
                refusals.append(
                    {
                        "id": pair_id,
                        "status": status,
                        "short_detail": short_problem,
                        "long_detail": long_problem,
                        "short_status": short["trades"][0]["status"] if short else None,
                        "long_status": long["trades"][0]["status"] if long else None,
                    }
                )
                continue
            short_trade, long_trade = short["trades"][0], long["trades"][0]
            trade = self._combine_trade(pair_id, short_trade, long_trade)
            pair_marks = self._combine_marks(pair_id, short["marks"], long["marks"])
            if (
                not pair_marks
                or abs(pair_marks[-1]["pnl_usd"] - trade["pnl_usd"]) > 1e-7
            ):
                raise ValueError(
                    f"{self.key}: paired marks do not reconcile for {pair_id!r}"
                )
            trades.append(trade)
            marks.extend(pair_marks)
            cashflows.extend(self._cashflows(pair_id, "short", short_trade))
            cashflows.extend(self._cashflows(pair_id, "long", long_trade))
        total = {
            name: sum(t[name] for t in trades)
            for name in (
                "pnl_usd",
                "fees_usd",
                "premium_usd",
                "option_pnl_usd",
                "hedge_pnl_usd",
            )
        }
        return {
            "trades": trades,
            "marks": marks,
            "cashflows": cashflows,
            "refusals": refusals,
            "summary": {
                "pairs_in": len(pairs),
                "pairs_closed": len(trades),
                "pairs_refused": len(refusals),
                "refused_by_reason": dict(reasons),
                "chain_rows_seen": compact["rows_seen"] if compact else 0,
                "chain_rows_retained": len(compact["retained"]) if compact else 0,
                "totals": total,
            },
        }

    def _compact_chain(self, source, prepared):
        """Read the source once, retaining only order contracts and a day-underlying row."""
        p = self.params["ledger_params"]
        order_fields, chain_fields = p["order_fields"], p["chain_fields"]
        wanted = set()
        bounds = {}
        for _pair_id, short, long, _short_n, _long_n, _hedge_rate in prepared:
            for order in (short, long):
                symbol = order.get(order_fields["symbol"])
                opened = str(order.get(order_fields["date"]))
                expiry = str(order.get(order_fields["expiration"]))
                name = order.get(order_fields["structure"])
                strikes = order.get(order_fields["strikes"])
                # The delegated ledger owns final order validity; this plan only narrows reads.
                if (
                    not isinstance(symbol, str)
                    or name not in p["structures"]
                    or not isinstance(strikes, (list, tuple))
                ):
                    raise ValueError(
                        f"{self.key}: order lacks a valid symbol, structure or strikes"
                    )
                legs = p["structures"][name]
                if len(strikes) != len(legs) or not all(number_ok(k) for k in strikes):
                    raise ValueError(
                        f"{self.key}: order strikes do not match its structure"
                    )
                wanted.update((symbol, expiry, float(strike)) for strike in strikes)
                previous = bounds.get(symbol)
                bounds[symbol] = (
                    (min(previous[0], opened), max(previous[1], expiry))
                    if previous
                    else (opened, expiry)
                )
        retained, quotes, sentinels, sentinel_good = {}, defaultdict(list), {}, {}
        rows_seen = 0
        for index, row in enumerate(source):
            rows_seen += 1
            if not isinstance(row, Mapping):
                raise ValueError(f"{self.key}: chain row {index} must be a mapping")
            symbol = row.get(chain_fields["symbol"])
            if symbol not in bounds:
                continue
            day = str(row.get(chain_fields["date"]))
            if not bounds[symbol][0] <= day <= bounds[symbol][1]:
                continue
            quote_key = None
            strike = row.get(chain_fields["strike"])
            if number_ok(strike):
                possible = (
                    symbol,
                    str(row.get(chain_fields["expiration"])),
                    float(strike),
                )
                if possible in wanted:
                    quote_key = possible
            sentinel_key = (symbol, day)
            under_good = price_ok(row.get(chain_fields["underlying"]))
            keep_sentinel = sentinel_key not in sentinels or (
                not sentinel_good[sentinel_key] and under_good
            )
            if quote_key is None and not keep_sentinel:
                continue
            # Carry only the delegated ledger's declared columns, not the full source row.
            retained[index] = {field: row.get(field) for field in chain_fields.values()}
            if keep_sentinel:
                sentinels[sentinel_key] = index
                sentinel_good[sentinel_key] = under_good
            if quote_key is not None:
                quotes[quote_key].append((day, index))
        by_symbol_days = defaultdict(list)
        for symbol, day in sentinels:
            by_symbol_days[symbol].append(day)
        for days in by_symbol_days.values():
            days.sort()
        return {
            "retained": retained,
            "quotes": quotes,
            "sentinels": sentinels,
            "by_symbol_days": by_symbol_days,
            "rows_seen": rows_seen,
        }

    def _pair_chain(self, compact, short_order, long_order):
        """Assemble compact delegated rows for the two requested contracts and lifetimes."""
        p = self.params["ledger_params"]
        f = p["order_fields"]
        indexes = set()
        for order in (short_order, long_order):
            symbol = order[f["symbol"]]
            opened, expiry = str(order[f["date"]]), str(order[f["expiration"]])
            for day in compact["by_symbol_days"].get(symbol, ()):
                if opened <= day <= expiry:
                    indexes.add(compact["sentinels"][symbol, day])
            for strike in order[f["strikes"]]:
                for day, index in compact["quotes"].get(
                    (symbol, expiry, float(strike)), ()
                ):
                    if opened <= day <= expiry:
                        indexes.add(index)
        return [compact["retained"][index] for index in sorted(indexes)]

    def _pair(self, pair, index):
        """Read one pair's identity, orders and independent contract counts."""
        if not isinstance(pair, Mapping):
            raise ValueError(f"{self.key}: pair {index} must be a mapping")
        f = {
            key: self.params.get(key, value)
            for key, value in self._DEFAULT_FIELDS.items()
        }
        pair_id = pair.get(f["pair_id_field"])
        if not isinstance(pair_id, str) or not pair_id:
            raise ValueError(f"{self.key}: pair {index} needs a non-empty id")
        orders = [
            pair.get(f[name]) for name in ("short_order_field", "long_order_field")
        ]
        if any(not isinstance(order, Mapping) for order in orders):
            raise ValueError(f"{self.key}: pair {pair_id!r} needs two order mappings")
        counts = [
            pair.get(f[name])
            for name in ("short_contracts_field", "long_contracts_field")
        ]
        for name, count in zip(
            ("short_contracts", "long_contracts"), counts, strict=True
        ):
            problems = []
            check_int_param(problems, name, count, ge=1)
            if problems:
                raise ValueError(f"{self.key}: pair {pair_id!r}: {problems[0]}")
        hedge_rate = pair.get(f["hedge_rate_field"])
        if hedge_rate is not None:
            if not number_ok(hedge_rate):
                raise ValueError(
                    f"{self.key}: pair {pair_id!r} hedge_rate must be finite"
                )
            if not isinstance(self.params["ledger_params"].get("hedge"), Mapping):
                raise ValueError(
                    f"{self.key}: pair {pair_id!r} hedge_rate needs ledger_params.hedge"
                )
        return pair_id, orders[0], orders[1], int(counts[0]), int(counts[1]), hedge_rate

    def _aligned(self, pair_id, short_order, long_order):
        """Refuse orders that cannot have one shared pair lifecycle."""
        fields = self.params["ledger_params"]["order_fields"]
        for role in (
            ("date", "expiration", "exit")
            if "exit" in fields
            else ("date", "expiration")
        ):
            field = fields[role]
            if short_order.get(field) != long_order.get(field):
                raise ValueError(f"{self.key}: pair {pair_id!r} mismatched {role}")
        if short_order.get(fields["symbol"]) == long_order.get(fields["symbol"]):
            raise ValueError(f"{self.key}: pair {pair_id!r} needs distinct symbols")

    def _leg(self, ctx, chain, order, contracts, hedge_rate):
        """Run the existing ledger for one structure, classifying expected quote refusal."""
        params = {**self.params["ledger_params"], "contracts": contracts}
        if hedge_rate is not None:
            params["hedge"] = {**params["hedge"], "rate": float(hedge_rate)}
        try:
            result = OptionStructureLedger(f"{self.key}_leg", params).run(
                ctx, {"chain": chain, "orders": [order]}
            )
        except ValueError as exc:
            message = str(exc)
            if "every order was refused" in message or "no chain rows" in message:
                return None, message
            raise
        return result, None

    @staticmethod
    def _status(short, long, short_problem, long_problem):
        """Say why a pair cannot be counted as one completed trade."""
        if short_problem and long_problem:
            return "both_refused"
        if short_problem:
            return "short_refused"
        if long_problem:
            return "long_refused"
        a, b = short["trades"][0], long["trades"][0]
        if (
            a["status"] != "closed"
            or b["status"] != "closed"
            or a["close_date"] != b["close_date"]
            or a["reason_closed"] != b["reason_closed"]
            or a["exit_refused"]
            or b["exit_refused"]
        ):
            return "not_closed"
        return None

    @staticmethod
    def _combine_trade(pair_id, short, long):
        """Sum delegated cash and exposure without repricing any contract."""
        fields = (
            "premium_usd",
            "credit_usd",
            "open_fees_usd",
            "close_cash_usd",
            "close_fees_usd",
            "option_pnl_usd",
            "hedge_pnl_usd",
            "hedge_fees_usd",
            "fees_usd",
            "pnl_usd",
        )
        result = {
            "id": pair_id,
            "open_date": short["open_date"],
            "close_date": short["close_date"],
            "reason_closed": short["reason_closed"],
            "short_trade": short,
            "long_trade": long,
            "gross_contracts": short["contracts"] + long["contracts"],
        }
        result.update({field: short[field] + long[field] for field in fields})
        return result

    @staticmethod
    def _combine_marks(pair_id, short_marks, long_marks):
        """Join daily running marks, carrying a closed or unquoted leg explicitly."""
        left = {row["date"]: row for row in short_marks}
        right = {row["date"]: row for row in long_marks}
        current_left = current_right = None
        out = []
        for day in sorted(left.keys() | right.keys()):
            next_left, next_right = left.get(day), right.get(day)
            current_left = next_left or current_left
            current_right = next_right or current_right
            if current_left is None or current_right is None:
                raise ValueError("paired marks do not share an opening date")
            out.append(
                {
                    "id": pair_id,
                    "date": day,
                    "short_pnl_usd": current_left["pnl_usd"],
                    "long_pnl_usd": current_right["pnl_usd"],
                    "pnl_usd": current_left["pnl_usd"] + current_right["pnl_usd"],
                    "hedge_shares": current_left["hedge_shares"]
                    + current_right["hedge_shares"],
                    "carried": bool(
                        next_left is None
                        or next_right is None
                        or current_left["carried"]
                        or current_right["carried"]
                    ),
                    "short_mark": current_left,
                    "long_mark": current_right,
                }
            )
        return out

    @staticmethod
    def _cashflows(pair_id, leg, trade):
        """Return structure-level fills and settlement plus delegated hedge accrual."""
        rows = [
            {
                "id": pair_id,
                "leg": leg,
                "phase": "open_fill",
                "date": trade["open_date"],
                "cash_usd": trade["premium_usd"],
                "fees_usd": trade["open_fees_usd"],
            },
            {
                "id": pair_id,
                "leg": leg,
                "phase": "exit_fill"
                if trade["reason_closed"] == "exit"
                else "expiry_settlement",
                "date": trade["close_date"],
                "cash_usd": trade["close_cash_usd"],
                "fees_usd": trade["close_fees_usd"],
            },
        ]
        if trade["hedge_pnl_usd"] or trade["hedge_fees_usd"]:
            rows.append(
                {
                    "id": pair_id,
                    "leg": leg,
                    "phase": "hedge_result",
                    "date": trade["close_date"],
                    "cash_usd": trade["hedge_pnl_usd"],
                    "fees_usd": trade["hedge_fees_usd"],
                }
            )
        return rows
