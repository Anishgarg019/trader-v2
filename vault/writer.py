"""Obsidian vault writer (spec §7).

The vault is just a folder of plain `.md` files. We operate it by reading/writing Markdown
with YAML frontmatter directly (Obsidian/Dataview/Templater are human-side). Every note
starts with frontmatter so Dataview can query it; field order and names follow spec
§7.2–§7.4 exactly. Cross-platform: all paths via pathlib (Mac dev → Windows prod).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml


class _NoneBlankDumper(yaml.SafeDumper):
    """Dump None as an empty scalar ('key:') rather than 'key: null', matching the spec
    templates' blank-until-filled fields."""


_NoneBlankDumper.add_representer(
    type(None),
    lambda dumper, _: dumper.represent_scalar("tag:yaml.org,2002:null", ""),
)

# Folder tree (spec §7).
SUBDIRS = [
    "Universe",
    "Strategies", "Strategies/Graveyard",
    "Trades",
    "Daily",
    "Reviews/Weekly", "Reviews/Monthly",
    "Research",
    "System/alerts",
]


def _dump_frontmatter(fm: dict[str, Any]) -> str:
    return yaml.dump(fm, Dumper=_NoneBlankDumper, sort_keys=False,
                     allow_unicode=True, default_flow_style=False)


class VaultWriter:
    def __init__(self, vault_path: str | Path):
        self.root = Path(vault_path).expanduser()

    # ---- structure -----------------------------------------------------------
    def ensure_structure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---- generic note I/O ----------------------------------------------------
    def write_note(self, relative_path: str | Path, frontmatter: dict[str, Any],
                   body: str = "") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"---\n{_dump_frontmatter(frontmatter)}---\n\n{body.rstrip()}\n"
        path.write_text(content, encoding="utf-8")
        return path

    def read_note(self, relative_path: str | Path) -> tuple[dict[str, Any], str]:
        path = self.root / relative_path
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}, text
        # split on the first two '---' fences
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip("\n") if len(parts) > 2 else ""
        return fm, body

    def update_frontmatter(self, relative_path: str | Path,
                           updates: dict[str, Any]) -> Path:
        fm, body = self.read_note(relative_path)
        fm.update(updates)
        return self.write_note(relative_path, fm, body)

    def exists(self, relative_path: str | Path) -> bool:
        return (self.root / relative_path).exists()

    # ---- path helpers --------------------------------------------------------
    @staticmethod
    def trade_rel(d: date | str, symbol: str, strategy_id: str) -> str:
        sym = str(symbol).replace(":", "_")
        return f"Trades/{d}-{sym}-{strategy_id}.md"

    @staticmethod
    def daily_rel(d: date | str) -> str:
        return f"Daily/{d}.md"

    @staticmethod
    def strategy_rel(strategy_id: str, name: str, graveyard: bool = False) -> str:
        folder = "Strategies/Graveyard" if graveyard else "Strategies"
        return f"{folder}/{strategy_id} - {name}.md"

    @staticmethod
    def alert_rel(d: date | str, slug: str) -> str:
        return f"System/alerts/{d}-{slug}.md"

    # ---- note builders (exact spec schemas) ----------------------------------
    def write_trade_note(self, *, d: date | str, symbol: str, strategy_id: str,
                         strategy_link: str, frontmatter_extra: dict | None = None,
                         justification: str = "", review: str = "") -> Path:
        """Spec §7.2 trade note (write at fill time)."""
        fm: dict[str, Any] = {
            "type": "trade", "date": str(d), "symbol": symbol,
            "strategy": strategy_link, "direction": "long", "product": "CNC",
            "entry_price": 0, "exit_price": None, "stop_price": 0, "target_price": None,
            "quantity": 0, "risk_rupees": 0, "atr_at_entry": 0, "size_pct_equity": 0,
            "status": "open", "outcome": None, "pnl_rupees": None, "pnl_R": None,
            "charges_rupees": None, "hold_bars": None, "regime": None,
            "mistake_tags": [], "order_tag": f"SYS-{strategy_id}",
        }
        if frontmatter_extra:
            fm.update(frontmatter_extra)
        default_just = (
            "- Ensemble that fired: <which signals agreed, with values>\n"
            "- Confirmation: <trend/ADX/volume gates satisfied>\n"
            "- Risk: R = <…>, stop = k×ATR = <…>, qty per §4 = <…>\n"
            "- Backtest basis: <link to strategy note + key OOS stats>"
        )
        default_review = (
            "- Did it behave as modeled? <yes/no — explain>\n"
            "- P&L attribution: <expected driver vs actual driver>\n"
            "- Anomalies / lessons: <…>"
        )
        body = (
            "## Justification (why this trade, before placing)\n"
            f"{justification or default_just}\n\n"
            "## Review (after close)\n"
            f"{review or default_review}\n"
        )
        return self.write_note(self.trade_rel(d, symbol, strategy_id), fm, body)

    def write_strategy_note(self, *, strategy_id: str, name: str, status: str = "researching",
                            families: list[str] | None = None, timeframe: str = "day",
                            created: date | str = "", params: dict | None = None,
                            backtest: dict | None = None, decay_check: str = "",
                            thesis: str = "", rules: str = "", conditions: str = "",
                            backtest_log: str = "", status_history: str = "",
                            graveyard: bool = False,
                            frontmatter_extra: dict | None = None) -> Path:
        """Spec §7.3 strategy note.

        `frontmatter_extra` lets Phase 11 attach the `spec:` block and `deployed_symbols:`
        list (the per-symbol allowlist) to the frontmatter (RESEARCHER-SPEC §6).
        """
        fm: dict[str, Any] = {
            "type": "strategy", "id": strategy_id, "name": name, "status": status,
            "families": families or [], "timeframe": timeframe, "created": str(created),
            "params": params or {},
            "backtest": backtest or {
                "period_in_sample": None, "period_out_sample": None, "return_pct": None,
                "max_dd_pct": None, "sharpe_like": None, "win_rate": None, "trades": None,
                "friction_modeled": True,
            },
            "decay_check": decay_check, "tags": ["strategy"],
        }
        if frontmatter_extra:
            fm.update(frontmatter_extra)
        default_rules = ("- Entry: <precise, parameterized>\n"
                         "- Exit: <precise>\n"
                         "- Sizing/stops: per spec §4 (atr_k above)")
        default_history = f"- {created} created ({status})"
        body = (
            f"## Thesis\n{thesis}\n\n"
            f"## Exact rules\n{rules or default_rules}\n\n"
            f"## Conditions where it works / decays\n{conditions}\n\n"
            f"## Backtest log\n{backtest_log}\n\n"
            f"## Status history\n{status_history or default_history}\n"
        )
        return self.write_note(self.strategy_rel(strategy_id, name, graveyard), fm, body)

    def write_daily_note(self, *, d: date | str, trading_day: bool = True,
                        holiday: str | None = None, day_open_equity: float = 100000,
                        frontmatter_extra: dict | None = None, body: str = "") -> Path:
        """Spec §7.4 daily note."""
        fm: dict[str, Any] = {
            "type": "daily", "date": str(d), "trading_day": trading_day,
            "holiday": holiday, "day_open_equity": day_open_equity,
            "day_close_equity": None, "realized_pnl": None, "open_risk_pct": None,
            "drawdown_day_pct": None, "drawdown_total_pct": None, "halted": False,
            "trades_today": [], "tags": ["daily"],
        }
        if frontmatter_extra:
            fm.update(frontmatter_extra)
        default_body = (
            "## Pre-market\n- Overnight/news/gaps:\n- Data integrity check:\n"
            "- Position reconciliation:\n- Risk preflight (day-open equity, 5% line):\n\n"
            "## Market hours\n- Fills & execution quality:\n- System health:\n"
            "- Exceptions/interventions (should be rare):\n\n"
            "## Post-market\n- Reconciliation:\n- P&L attribution:\n- Anomalies:\n\n"
            "## Research today\n- Hypothesis / backtest / OOS result:\n"
            "- Decay check on live strategies:\n"
        )
        return self.write_note(self.daily_rel(d), fm, body or default_body)

    def write_system_alert(self, *, d: date | str, slug: str, kind: str,
                           detail: str = "") -> Path:
        """System/alerts note for risk-halts, data anomalies, sandbox/leverage safety, etc."""
        fm = {"type": "system-alert", "date": str(d), "kind": kind, "tags": ["alert"]}
        body = f"## {kind}\n{detail}\n"
        return self.write_note(self.alert_rel(d, slug), fm, body)
