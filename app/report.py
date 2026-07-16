"""Build the HTML weekly digest for the Risk Monitor."""
import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from .db import Account, BitcoinLoan, Position, SessionLocal


def _num(v, decimals=0):
    if v is None:
        return "&mdash;"
    return f"{v:,.{decimals}f}"


def _btc(v):
    if v is None:
        return "&mdash;"
    return f"{v:,.4f}"


def _pnl_color(v):
    if v is None or v == 0:
        return "#334155"
    return "#166534" if v > 0 else "#991b1b"


def _current_btc_price(session) -> float | None:
    stmt = select(Position).where(Position.symbol == "BTC", Position.last_price.is_not(None))
    for pos in session.execute(stmt).scalars():
        if pos.last_price:
            return float(pos.last_price)
    return None


def build_digest() -> tuple[str, str, str]:
    """Return (subject, plaintext_body, html_body) for the weekly digest."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Position, Account).join(Account, Position.account_id == Account.id)
        ).all()

        agg: dict[str, dict] = defaultdict(lambda: {
            "symbol": "",
            "quantity": 0.0,
            "market_value": 0.0,
            "cost_basis_value": 0.0,
            "has_basis": False,
        })
        for pos, _ in rows:
            key = pos.symbol
            a = agg[key]
            a["symbol"] = key
            a["quantity"] += pos.quantity or 0
            a["market_value"] += pos.market_value or 0
            if pos.cost_basis_value is not None:
                a["cost_basis_value"] += pos.cost_basis_value
                a["has_basis"] = True

        symbols = [s for s in agg.values() if round(s["market_value"] or 0) != 0]
        symbols.sort(key=lambda x: -(x.get("market_value") or 0))

        total_mv = sum(s["market_value"] for s in symbols)
        total_cb = sum(s["cost_basis_value"] for s in symbols if s["has_basis"])
        total_pnl = sum(
            s["market_value"] - s["cost_basis_value"]
            for s in symbols if s["has_basis"]
        )

        btc_price = _current_btc_price(session)

        loans = session.execute(select(BitcoinLoan)).scalars().all()
        loan_summary = None
        if loans:
            total_principal = sum(l.outstanding_principal or 0 for l in loans)
            total_interest = sum(l.interest_accrued or 0 for l in loans)
            total_collateral = sum(l.collateral_btc or 0 for l in loans)
            total_debt = total_principal + total_interest
            collateral_value = total_collateral * btc_price if btc_price else None
            debt_btc = total_debt / btc_price if btc_price else None
            net_btc = total_collateral - debt_btc if debt_btc is not None else None
            net_mv = net_btc * btc_price if net_btc is not None and btc_price else None
            ltv = total_debt / collateral_value if collateral_value else None
            loan_summary = {
                "count": len(loans),
                "principal": total_principal,
                "interest": total_interest,
                "collateral": total_collateral,
                "debt_btc": debt_btc,
                "net_btc": net_btc,
                "net_mv": net_mv,
                "ltv": ltv,
            }

        btc_total = 0.0
        ibit_total_mv = 0.0
        for s in symbols:
            if s["symbol"] == "BTC":
                btc_total += s["quantity"]
            elif s["symbol"] == "IBIT":
                ibit_total_mv += s["market_value"]
        virtual_btc_from_ibit = ibit_total_mv / btc_price if btc_price else 0
        virtual_btc_total = btc_total + virtual_btc_from_ibit
        if loan_summary and loan_summary["net_btc"] is not None:
            virtual_btc_total += loan_summary["net_btc"]

    now = dt.datetime.now()
    subject = f"Risk Monitor digest — {now.strftime('%b %d, %Y')}"

    top_positions = symbols[:15]
    plain_lines = [
        f"Risk Monitor weekly digest — {now.strftime('%B %d, %Y')}",
        "",
        f"Total portfolio value: ${_num(total_mv, 0).replace('&mdash;', '—')}",
        f"Total cost basis: ${_num(total_cb, 0).replace('&mdash;', '—')}",
        f"Total unrealized P&L: ${_num(total_pnl, 0).replace('&mdash;', '—')}",
        "",
        f"Virtual BTC exposure: {virtual_btc_total:,.4f} BTC",
        f"Reference BTC price: ${_num(btc_price, 0).replace('&mdash;', '—')}" if btc_price else "Reference BTC price: unknown",
        "",
    ]
    if loan_summary:
        plain_lines += [
            f"Bitcoin loans: {loan_summary['count']} active",
            f"  Debt (principal + interest): ${_num(loan_summary['principal'] + loan_summary['interest'], 0)}",
            f"  Collateral: {loan_summary['collateral']:.4f} BTC",
            f"  Net (BTC): {_btc(loan_summary['net_btc']).replace('&mdash;', '—')}",
            f"  Aggregate LTV: {(loan_summary['ltv'] * 100):.2f}%" if loan_summary["ltv"] is not None else "  Aggregate LTV: —",
            "",
        ]
    plain_lines.append("Top positions:")
    for s in top_positions:
        pnl = s["market_value"] - s["cost_basis_value"] if s["has_basis"] else None
        pnl_str = f"P&L ${_num(pnl, 0)}" if pnl is not None else "P&L —"
        plain_lines.append(f"  {s['symbol']:<10}  MV ${_num(s['market_value'], 0):>15}  {pnl_str}")
    plain_lines += ["", "Live dashboard: http://riskmon:8000/main"]
    plain = "\n".join(l.replace("&mdash;", "—") for l in plain_lines)

    def row(label, value, color="#334155", bold=False):
        v_style = f"color:{color};font-weight:{'700' if bold else '400'};text-align:right;padding:6px 12px"
        return f'<tr><td style="padding:6px 12px;color:#475569">{label}</td><td style="{v_style}">{value}</td></tr>'

    def hdr(title):
        return f'<h2 style="font-family:-apple-system,Segoe UI,sans-serif;font-size:16px;color:#0f172a;margin:24px 0 8px 0">{title}</h2>'

    style_body = "font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;color:#0f172a;max-width:720px;margin:0 auto;padding:24px"
    style_summary_tbl = "border-collapse:collapse;width:100%;background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;font-family:-apple-system,Segoe UI,sans-serif;font-size:14px"
    style_pos_tbl = "border-collapse:collapse;width:100%;font-family:-apple-system,Segoe UI,sans-serif;font-size:13px"

    positions_rows = ""
    for s in top_positions:
        pnl = s["market_value"] - s["cost_basis_value"] if s["has_basis"] else None
        pnl_html = _num(pnl, 0) if pnl is not None else "&mdash;"
        pnl_style = f"color:{_pnl_color(pnl)};font-weight:600;text-align:right;padding:6px 12px"
        positions_rows += (
            f'<tr>'
            f'<td style="padding:6px 12px;font-weight:600">{s["symbol"]}</td>'
            f'<td style="padding:6px 12px;text-align:right">{_num(s["quantity"], 0)}</td>'
            f'<td style="padding:6px 12px;text-align:right">${_num(s["market_value"], 0)}</td>'
            f'<td style="{pnl_style}">${pnl_html}</td>'
            f'</tr>'
        )

    loan_html = ""
    if loan_summary:
        ltv_pct = f"{loan_summary['ltv'] * 100:.2f}%" if loan_summary["ltv"] is not None else "&mdash;"
        loan_html = (
            hdr(f"Bitcoin loans ({loan_summary['count']} active)")
            + f'<table style="{style_summary_tbl}">'
            + row("Total debt", f"${_num(loan_summary['principal'] + loan_summary['interest'], 0)}")
            + row("Collateral", f"{loan_summary['collateral']:.4f} BTC")
            + row("Debt (BTC)", _btc(loan_summary['debt_btc']))
            + row("Net (BTC)", _btc(loan_summary['net_btc']), bold=True)
            + row("Net value", f"${_num(loan_summary['net_mv'], 0)}", bold=True)
            + row("Aggregate LTV", ltv_pct)
            + '</table>'
        )

    pnl_color = _pnl_color(total_pnl)
    html = f"""<!doctype html>
<html><body style="{style_body}">
  <h1 style="font-size:20px;color:#0f172a;margin:0 0 4px 0">Risk Monitor weekly digest</h1>
  <p style="color:#64748b;margin:0 0 20px 0">{now.strftime('%A, %B %d, %Y')}</p>

  {hdr("Portfolio summary")}
  <table style="{style_summary_tbl}">
    {row("Total market value", f"${_num(total_mv, 0)}", bold=True)}
    {row("Total cost basis", f"${_num(total_cb, 0)}")}
    {row("Unrealized P&L", f"${_num(total_pnl, 0)}", color=pnl_color, bold=True)}
    {row("Virtual BTC exposure", f"{virtual_btc_total:,.4f} BTC", bold=True)}
    {row("Reference BTC price", f"${_num(btc_price, 0)}" if btc_price else "&mdash;")}
  </table>

  {loan_html}

  {hdr("Top positions by market value")}
  <table style="{style_pos_tbl}">
    <thead>
      <tr style="background:#f1f5f9">
        <th style="padding:6px 12px;text-align:left">Symbol</th>
        <th style="padding:6px 12px;text-align:right">Quantity</th>
        <th style="padding:6px 12px;text-align:right">Market value</th>
        <th style="padding:6px 12px;text-align:right">P&L</th>
      </tr>
    </thead>
    <tbody>
      {positions_rows}
    </tbody>
  </table>

  <p style="color:#94a3b8;font-size:12px;margin-top:32px">
    Live dashboard: <a href="http://riskmon:8000/main" style="color:#2563eb">http://riskmon:8000/main</a>
  </p>
</body></html>"""

    return subject, plain, html


def _plain(v, decimals=0):
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"


def build_pushover_summary() -> tuple[str, str]:
    """Return (title, message) — a compact push summary that fits Pushover's
    1024-char message limit. Same underlying figures as the full digest."""
    with SessionLocal() as session:
        rows = session.execute(select(Position)).scalars().all()
        agg: dict[str, dict] = defaultdict(lambda: {
            "symbol": "", "quantity": 0.0, "market_value": 0.0,
            "cost_basis_value": 0.0, "has_basis": False,
        })
        for pos in rows:
            a = agg[pos.symbol]
            a["symbol"] = pos.symbol
            a["quantity"] += pos.quantity or 0
            a["market_value"] += pos.market_value or 0
            if pos.cost_basis_value is not None:
                a["cost_basis_value"] += pos.cost_basis_value
                a["has_basis"] = True

        symbols = [s for s in agg.values() if round(s["market_value"] or 0) != 0]
        symbols.sort(key=lambda x: -(x["market_value"] or 0))
        total_mv = sum(s["market_value"] for s in symbols)
        total_pnl = sum(
            s["market_value"] - s["cost_basis_value"] for s in symbols if s["has_basis"]
        )
        btc_price = _current_btc_price(session)

        loans = session.execute(select(BitcoinLoan)).scalars().all()
        loan_count = len(loans)
        ltv = None
        net_btc = None
        if loans:
            total_debt = sum((l.outstanding_principal or 0) + (l.interest_accrued or 0) for l in loans)
            total_collateral = sum(l.collateral_btc or 0 for l in loans)
            collateral_value = total_collateral * btc_price if btc_price else None
            ltv = total_debt / collateral_value if collateral_value else None
            debt_btc = total_debt / btc_price if btc_price else None
            net_btc = total_collateral - debt_btc if debt_btc is not None else None

        btc_total = sum(s["quantity"] for s in symbols if s["symbol"] == "BTC")
        ibit_mv = sum(s["market_value"] for s in symbols if s["symbol"] == "IBIT")
        virtual_btc = btc_total + (ibit_mv / btc_price if btc_price else 0)
        if net_btc is not None:
            virtual_btc += net_btc

    now = dt.datetime.now()
    title = f"Risk Monitor — ${_plain(total_mv)}"

    def _signed(v):
        """Colored, signed dollar amount using Pushover's <font> HTML."""
        if v is None:
            return "—"
        color = "#16a34a" if v >= 0 else "#dc2626"
        sign = "+" if v >= 0 else "-"
        return f'<font color="{color}">{sign}${_plain(abs(v))}</font>'

    lines = [
        f"<b>{now.strftime('%A, %b %d, %Y')}</b>",
        f"<b>Total value:</b> ${_plain(total_mv)}",
        f"<b>Unrealized P/L:</b> {_signed(total_pnl)}",
        f"<b>Virtual BTC:</b> {virtual_btc:,.4f}"
        + (f' <font color="#64748b">(BTC ${_plain(btc_price)})</font>' if btc_price else ""),
    ]
    if loan_count:
        if ltv is None:
            ltv_str = "—"
        else:
            ltv_str = f"{ltv * 100:.1f}%"
            if ltv >= 0.7:  # flag an elevated loan-to-value in red
                ltv_str = f'<font color="#dc2626">{ltv_str}</font>'
        lines.append(f"<b>Loans:</b> {loan_count} · LTV {ltv_str}")
    lines.append("")
    lines.append("<b>Top positions</b>")
    for s in symbols[:5]:
        pnl = (s["market_value"] - s["cost_basis_value"]) if s["has_basis"] else None
        pnl_str = f"  {_signed(pnl)}" if pnl is not None else ""
        lines.append(f"{s['symbol']}  ${_plain(s['market_value'])}{pnl_str}")

    return title, "\n".join(lines)[:1024]
