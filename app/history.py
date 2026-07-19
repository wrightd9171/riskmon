"""Daily portfolio history for the Trending view.

record_snapshot() upserts today's per-position rows from the current positions
(called after each refresh and by the digest). trend_data() reshapes the stored
history into date-aligned line series carrying both metrics; the UI picks the
metric and toggles which series show.
"""
import datetime as dt

from sqlalchemy import select

from .db import Account, Position, PositionSnapshot, SessionLocal

TOP_SYMBOLS = 8  # symbol series show the top N by latest value, rest -> "Other"

BROKER_LABEL = {
    "schwab": "Schwab", "coinbase": "Coinbase", "robinhood": "Robinhood",
    "strike": "Strike", "fidelity": "Fidelity", "onchain": "On-chain",
}
BROKER_ORDER = ["schwab", "coinbase", "robinhood", "strike", "fidelity", "onchain"]


def _account_label(a: Account) -> str:
    if a.broker == "fidelity":
        return f"Fidelity {a.account_type}" if a.account_type else "Fidelity"
    if a.broker == "onchain":
        return a.account_type or "On-chain"
    if a.broker in ("coinbase", "strike", "robinhood"):
        return a.broker.title()
    parts = ["Schwab"]
    if a.account_type:
        parts.append(a.account_type.title().replace("_", " "))
    if a.account_number_masked:
        parts.append(a.account_number_masked)
    return " ".join(parts)


def record_snapshot() -> None:
    """Upsert today's snapshot from the current positions (idempotent per day)."""
    today = dt.date.today()
    with SessionLocal() as session:
        session.query(PositionSnapshot).filter(
            PositionSnapshot.snapshot_date == today
        ).delete()
        rows = session.execute(
            select(Position, Account).join(Account, Position.account_id == Account.id)
        ).all()
        for pos, acct in rows:
            if pos.market_value is None:
                continue
            session.add(PositionSnapshot(
                snapshot_date=today,
                broker=acct.broker,
                account_label=_account_label(acct),
                symbol=pos.symbol,
                market_value=pos.market_value,
                cost_basis_value=pos.cost_basis_value,
            ))
        session.commit()


def trend_data() -> dict:
    """Return {dates, series} where each series carries BOTH metrics:
    {id, label, group, mv:[...], pnl:[...]} aligned to `dates` (null = no data /
    undefined P&L). Series: Total (portfolio), one per broker (accounts), and the
    top symbols + Other."""
    with SessionLocal() as session:
        rows = session.execute(
            select(PositionSnapshot).order_by(PositionSnapshot.snapshot_date)
        ).scalars().all()

    dates = sorted({r.snapshot_date for r in rows})
    n = len(dates)
    di = {d: i for i, d in enumerate(dates)}

    def new_acc():
        return {"mv": [0.0] * n, "mv_seen": [False] * n,
                "pnl": [0.0] * n, "pnl_seen": [False] * n}

    total = new_acc()
    brokers: dict[str, dict] = {}
    symbols: dict[str, dict] = {}

    for r in rows:
        i = di[r.snapshot_date]
        mv = r.market_value
        for acc in (total,
                    brokers.setdefault(r.broker or "?", new_acc()),
                    symbols.setdefault(r.symbol, new_acc())):
            if mv is not None:
                acc["mv"][i] += mv
                acc["mv_seen"][i] = True
            if mv is not None and r.cost_basis_value is not None:
                acc["pnl"][i] += mv - r.cost_basis_value
                acc["pnl_seen"][i] = True

    def add_into(src, dst):
        for i in range(n):
            if src["mv_seen"][i]:
                dst["mv"][i] += src["mv"][i]
                dst["mv_seen"][i] = True
            if src["pnl_seen"][i]:
                dst["pnl"][i] += src["pnl"][i]
                dst["pnl_seen"][i] = True

    def finalize(acc, sid, label, group):
        return {
            "id": sid, "label": label, "group": group,
            "mv": [acc["mv"][i] if acc["mv_seen"][i] else None for i in range(n)],
            "pnl": [acc["pnl"][i] if acc["pnl_seen"][i] else None for i in range(n)],
        }

    series = [finalize(total, "total", "Total portfolio", "portfolio")]

    ordered = [b for b in BROKER_ORDER if b in brokers]
    ordered += [b for b in brokers if b not in BROKER_ORDER]
    for b in ordered:
        series.append(finalize(brokers[b], "broker:" + b, BROKER_LABEL.get(b, b.title()), "account"))

    if symbols:
        last = n - 1
        sym_keys = sorted(
            symbols,
            key=lambda s: (symbols[s]["mv"][last] if n and symbols[s]["mv_seen"][last] else 0.0),
            reverse=True,
        )
        for s in sym_keys[:TOP_SYMBOLS]:
            series.append(finalize(symbols[s], "sym:" + s, s, "symbol"))
        rest = sym_keys[TOP_SYMBOLS:]
        if rest:
            other = new_acc()
            for s in rest:
                add_into(symbols[s], other)
            series.append(finalize(other, "sym:__other", "Other symbols", "symbol"))

    return {"dates": [d.isoformat() for d in dates], "series": series}
