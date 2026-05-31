"""Review-note generation (spec §6.5): weekly, monthly, lessons-learned.

Forces the structured reflection that's easy to skip. Aggregation is deterministic Python;
the agent adds narrative. Trades are dicts (e.g. read from vault trade notes) carrying at
least 'symbol', 'strategy', and a P&L field ('pnl_rupees' or 'pnl').
"""
from __future__ import annotations

from typing import Any

from vault.writer import VaultWriter


def _pnl(t: dict) -> float:
    v = t.get("pnl_rupees", t.get("pnl"))
    return float(v) if v is not None else 0.0


def _strategy(t: dict) -> str:
    return str(t.get("strategy") or t.get("strategy_id") or "unknown")


def summarize_trades(trades: list[dict]) -> dict[str, Any]:
    """Aggregate closed trades into headline stats + per-strategy breakdown."""
    closed = [t for t in trades if t.get("status", "closed") == "closed"]
    n = len(closed)
    wins = [t for t in closed if _pnl(t) > 0]
    losses = [t for t in closed if _pnl(t) < 0]
    total = sum(_pnl(t) for t in closed)

    by_strategy: dict[str, dict] = {}
    for t in closed:
        s = _strategy(t)
        b = by_strategy.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
        b["trades"] += 1
        b["wins"] += 1 if _pnl(t) > 0 else 0
        b["pnl"] += _pnl(t)

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n if n else 0.0,
        "total_pnl": round(total, 2),
        "by_strategy": by_strategy,
    }


def _strategy_table(summary: dict) -> str:
    lines = ["| strategy | trades | win rate | P&L (₹) |", "| --- | ---: | ---: | ---: |"]
    for s, b in sorted(summary["by_strategy"].items()):
        wr = b["wins"] / b["trades"] if b["trades"] else 0.0
        lines.append(f"| {s} | {b['trades']} | {wr:.0%} | {b['pnl']:,.2f} |")
    return "\n".join(lines)


def write_weekly_review(writer: VaultWriter, *, year: int, week: int,
                        trades: list[dict], notes: str = "") -> "Path":  # noqa: F821
    s = summarize_trades(trades)
    fm = {"type": "review-weekly", "period": f"{year}-W{week:02d}",
          "trades": s["trades"], "win_rate": round(s["win_rate"], 3),
          "total_pnl": s["total_pnl"], "tags": ["review", "weekly"]}
    body = (f"## Week {year}-W{week:02d}\n\n"
            f"- Trades: {s['trades']} | Win rate: {s['win_rate']:.0%} | "
            f"P&L: ₹{s['total_pnl']:,.2f}\n\n"
            f"## By strategy\n{_strategy_table(s)}\n\n"
            f"## What worked / decayed\n{notes}\n")
    return writer.write_note(f"Reviews/Weekly/{year}-W{week:02d}.md", fm, body)


def write_monthly_review(writer: VaultWriter, *, year: int, month: int,
                         trades: list[dict], notes: str = "") -> "Path":  # noqa: F821
    s = summarize_trades(trades)
    fm = {"type": "review-monthly", "period": f"{year}-{month:02d}",
          "trades": s["trades"], "win_rate": round(s["win_rate"], 3),
          "total_pnl": s["total_pnl"], "tags": ["review", "monthly"]}
    body = (f"## {year}-{month:02d}\n\n"
            f"- Trades: {s['trades']} | Win rate: {s['win_rate']:.0%} | "
            f"P&L: ₹{s['total_pnl']:,.2f}\n\n"
            f"## By strategy\n{_strategy_table(s)}\n\n"
            f"## Strategy health / universe changes / drawdowns\n{notes}\n")
    return writer.write_note(f"Reviews/Monthly/{year}-{month:02d}.md", fm, body)


def append_lesson(writer: VaultWriter, lesson: str, *, d: str = "") -> "Path":  # noqa: F821
    """Append a dated bullet to Reviews/Lessons Learned.md (creates it if absent)."""
    rel = "Reviews/Lessons Learned.md"
    entry = f"- {d + ': ' if d else ''}{lesson}"
    if writer.exists(rel):
        fm, body = writer.read_note(rel)
        body = body.rstrip() + "\n" + entry
    else:
        fm = {"type": "lessons", "tags": ["review", "lessons"]}
        body = "## Lessons learned\n" + entry
    return writer.write_note(rel, fm, body)
