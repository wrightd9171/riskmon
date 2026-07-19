"""Daily portfolio history for the Trending view.

record_snapshot() upserts today's per-position rows from the current positions
(called after each refresh and by the digest). trend_data() reshapes the stored
history into date-aligned line series for a given breakdown.
"""
import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from .db import Account, Position, PositionSnapshot, SessionLocal

TOP_SYMBOLS = 8  # symbol breakdown shows the top N by latest value, rest -> "Other"


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


def _key_for(row: PositionSnapshot, breakdown: str) -> str:
    if breakdown == "account":
        return row.account_label or (row.broker or "?").title()
    if breakdown == "symbol":
        return row.symbol
    return "Total"


def trend_data(breakdown: str = "overall") -> dict:
    """Return {dates, mv_series, pnl_series} for the given breakdown.

    Each series is {label, values}, values aligned to `dates` (null = no data /
    undefined P&L that day). market value sums every position; P&L sums only
    positions that have a cost basis (null when a group has none that day)."""
    if breakdown not in ("overall", "account", "symbol"):
        breakdown = "overall"

    with SessionLocal() as session:
        rows = session.execute(
            select(PositionSnapshot).order_by(PositionSnapshot.snapshot_date)
        ).scalars().all()

    dates = sorted({r.snapshot_date for r in rows})
    # key -> date -> accumulators
    mv = defaultdict(lambda: defaultdict(float))
    pnl = defaultdict(lambda: defaultdict(float))
    pnl_has = defaultdict(lambda: defaultdict(bool))
    for r in rows:
        key = _key_for(r, breakdown)
        d = r.snapshot_date
        if r.market_value is not None:
            mv[key][d] += r.market_value
        if r.cost_basis_value is not None and r.market_value is not None:
            pnl[key][d] += r.market_value - r.cost_basis_value
            pnl_has[key][d] = True

    keys = list(mv.keys())
    # For symbol breakdown, keep the top N by latest market value; fold rest.
    if breakdown == "symbol" and len(keys) > TOP_SYMBOLS and dates:
        last = dates[-1]
        keys.sort(key=lambda k: mv[k].get(last, 0.0), reverse=True)
        top, rest = keys[:TOP_SYMBOLS], keys[TOP_SYMBOLS:]
        for k in rest:
            for d, v in mv[k].items():
                mv["Other"][d] += v
            for d, v in pnl[k].items():
                pnl["Other"][d] += v
                pnl_has["Other"][d] = True
            del mv[k]
            del pnl[k]
        keys = top + (["Other"] if rest else [])

    def mv_series():
        out = []
        for k in keys:
            byd = mv[k]
            out.append({"label": k, "values": [byd.get(d) for d in dates]})
        return out

    def pnl_series():
        out = []
        for k in keys:
            byd, hasd = pnl[k], pnl_has[k]
            out.append({
                "label": k,
                "values": [(byd.get(d) if hasd.get(d) else None) for d in dates],
            })
        # drop series that are entirely null (no cost basis anywhere)
        return [s for s in out if any(v is not None for v in s["values"])]

    return {
        "dates": [d.isoformat() for d in dates],
        "mv_series": mv_series(),
        "pnl_series": pnl_series(),
        "breakdown": breakdown,
    }
