#!/usr/bin/env python3
"""
Crude Oil Futures Trader UI — run this instead of trader.py directly.

  python3 ui.py

Controls
  ▶ Start        — connect to IB Gateway and begin the polling loop
  ⏸ Pause        — suspend between cycles (current cycle finishes first)
  ▶ Resume       — unpause; runs a new cycle immediately
  ⏹ Stop         — stop trading, keep the UI open
  ✎ Edit Prompt  — open the Grok prompt editor
  Q              — stop trading and exit
"""

import logging
import queue
import threading
import time
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static, TextArea

import trader
from trader import run_cycle


# ── Shared state ──────────────────────────────────────────────────────────────

_log_queue: "queue.Queue[str]" = queue.Queue()
_stop_event  = threading.Event()
_pause_event = threading.Event()
_countdown   = [0]


# ── Log routing ───────────────────────────────────────────────────────────────

class _QueueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_queue.put(self.format(record))


def _configure_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%H:%M:%S")
    q_handler    = _QueueHandler()
    q_handler.setFormatter(fmt)
    file_handler = logging.FileHandler("trader.log")
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(q_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)


# ── Trading thread ────────────────────────────────────────────────────────────

def _trading_loop() -> None:
    log = logging.getLogger(__name__)
    log.info("Connecting to IB Gateway at %s:%d…", trader.IB_HOST, trader.IB_PORT)
    try:
        trader.get_ib()
    except Exception as exc:
        log.error("Failed to connect to IB Gateway: %s", exc)
        log.error("Make sure IB Gateway is running with API enabled on port %d.", trader.IB_PORT)
        return

    log.info("Trader started | %s | interval=%ds", trader.FUTURES_SYMBOL, trader.POLL_INTERVAL_SECONDS)

    while not _stop_event.is_set():
        while _pause_event.is_set():
            if _stop_event.is_set():
                return
            time.sleep(0.25)
        if _stop_event.is_set():
            break

        _countdown[0] = 0
        run_cycle()

        sleep_secs = int(trader.POLL_INTERVAL_SECONDS)
        for remaining in range(sleep_secs, 0, -1):
            _countdown[0] = remaining
            if _stop_event.is_set():
                return
            if _pause_event.is_set():
                _countdown[0] = 0
                break
            time.sleep(1)
        _countdown[0] = 0

    log.info("Trader stopped.")


# ── Prompt editor modal ───────────────────────────────────────────────────────

class PromptModal(ModalScreen):

    CSS = """
    PromptModal { align: center middle; }

    #pm-dialog {
        width: 90%;
        height: 88%;
        background: $surface;
        border: thick $primary;
        layout: vertical;
    }
    #pm-title {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        padding: 0 2;
    }
    #pm-subtitle {
        height: 2;
        padding: 0 2;
        background: $boost;
        color: $text-muted;
        content-align: left middle;
    }
    #pm-area {
        height: 1fr;
        margin: 1 2;
        border: tall $accent;
    }
    #pm-area:focus { border: tall $success; }
    #pm-buttons {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 0 2;
    }
    """

    BINDINGS = [("escape", "action_cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="pm-dialog"):
            yield Static("  Edit Grok Prompt", id="pm-title")
            yield Static("  Sent to Grok every cycle — changes take effect on the next cycle", id="pm-subtitle")
            yield TextArea(trader.PROMPT, id="pm-area", soft_wrap=True, show_line_numbers=False)
            with Horizontal(id="pm-buttons"):
                yield Button("Save",   id="pm-save",   variant="success")
                yield Button("Cancel", id="pm-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pm-save":
            text = self.query_one("#pm-area", TextArea).text.strip()
            self.dismiss(text if text else None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Textual app ───────────────────────────────────────────────────────────────

_STOPPED = "stopped"
_RUNNING = "running"
_PAUSED  = "paused"


class TraderApp(App):

    CSS = """
    Screen { background: $surface; }

    #status-bar {
        height: 3;
        background: $boost;
        border: tall $primary;
        padding: 0 2;
        layout: horizontal;
        align: center middle;
    }
    #status-label   { width: 20; }
    #position-label { width: 1fr; content-align: center middle; }
    #next-label     { width: 1fr; text-align: right; }

    #controls {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 1 0;
    }
    Button { min-width: 18; margin: 0 1; }

    #settings {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 0 2;
        background: $panel;
        border: tall $primary;
    }
    .setting-group {
        width: auto;
        height: auto;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
    }
    .setting-label {
        width: auto;
        margin-right: 1;
        content-align: left middle;
    }
    Input {
        width: 20;
        border: tall $accent;
    }
    Input:focus   { border: tall $success; }
    Input.invalid { border: tall $error; }

    #position-panel {
        height: auto;
        border: tall $primary;
        background: $panel;
        padding: 0 1;
    }
    #position-header {
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    #position-content {
        padding: 0 1;
    }

    #log-header {
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    #log-wrap { height: 1fr; border: tall $primary; }
    RichLog   { padding: 0 1; }
    """

    TITLE = "CLN26 Crude Oil Futures Trader"
    BINDINGS = [
        ("s", "start",        "Start"),
        ("p", "pause_resume", "Pause / Resume"),
        ("t", "stop_trading", "Stop"),
        ("e", "edit_prompt",  "Edit Prompt"),
        ("x", "exit_all",     "Exit All"),
        ("q", "quit_app",     "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._state  = _STOPPED
        self._thread: threading.Thread | None = None

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Label("[red]⬤[/red]  STOPPED", id="status-label"),
            Label("FLAT",                  id="position-label"),
            Label("",                      id="next-label"),
            id="status-bar",
        )
        yield Horizontal(
            Button("▶  Start",       id="btn-start",       variant="success"),
            Button("⏸  Pause",       id="btn-pause",       variant="warning", disabled=True),
            Button("⏹  Stop",        id="btn-stop",        variant="error",   disabled=True),
            Button("✎  Edit Prompt", id="btn-edit-prompt", variant="primary"),
            Button("⚠  Exit All",    id="btn-exit-all",    variant="error"),
            id="controls",
        )
        yield Horizontal(
            Horizontal(
                Label("Symbol", classes="setting-label"),
                Input(value=trader.FUTURES_SYMBOL, id="input-symbol"),
                classes="setting-group",
            ),
            Horizontal(
                Label("Max Contracts", classes="setting-label"),
                Input(value=str(trader.MAX_CONTRACTS),
                      placeholder="1",
                      id="input-max-contracts"),
                classes="setting-group",
            ),
            Horizontal(
                Label("Interval (s)", classes="setting-label"),
                Input(value=str(trader.POLL_INTERVAL_SECONDS),
                      placeholder="300",
                      id="input-interval"),
                classes="setting-group",
            ),
            id="settings",
        )
        yield Static("  Position", id="position-header")
        with Container(id="position-panel"):
            yield Static("No position.", id="position-content")
        yield Static("  Activity Log", id="log-header")
        with Container(id="log-wrap"):
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        _configure_logging()
        self.set_interval(0.4, self._drain_log_queue)
        self.set_interval(1.0, self._refresh_status)

    # ── Input handlers ────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input-symbol":
            result = trader.parse_futures_symbol(event.value)
            if result is not None:
                trader.FUTURES_SYMBOL, trader.IB_CONTRACT_SYMBOL, trader.IB_CONTRACT_EXPIRY = result
                event.input.remove_class("invalid")
            else:
                event.input.add_class("invalid")

        elif event.input.id == "input-max-contracts":
            try:
                val = int(event.value.strip())
                if val >= 1:
                    trader.MAX_CONTRACTS = val
                    event.input.remove_class("invalid")
                else:
                    event.input.add_class("invalid")
            except ValueError:
                event.input.add_class("invalid")

        elif event.input.id == "input-interval":
            try:
                secs = int(event.value.strip())
                if secs > 0:
                    trader.POLL_INTERVAL_SECONDS = secs
                    event.input.remove_class("invalid")
                else:
                    event.input.add_class("invalid")
            except ValueError:
                event.input.add_class("invalid")

    # ── Button handler ────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "btn-start":       self.action_start,
            "btn-pause":       self.action_pause_resume,
            "btn-stop":        self.action_stop_trading,
            "btn-edit-prompt": self.action_edit_prompt,
            "btn-exit-all":    self.action_exit_all,
        }.get(event.button.id, lambda: None)()

    # ── Timers ────────────────────────────────────────────────────────────────

    def _drain_log_queue(self) -> None:
        log_widget = self.query_one("#log", RichLog)
        while not _log_queue.empty():
            try:
                log_widget.write(_log_queue.get_nowait())
            except queue.Empty:
                break

    def _refresh_status(self) -> None:
        self._refresh_position_label()
        self._refresh_next_label()
        self._refresh_position_panel()

    def _refresh_position_label(self) -> None:
        label    = self.query_one("#position-label", Label)
        contracts = trader.position_cache.get("contracts", 0)
        if contracts > 0:
            label.update(f"[green]{contracts} contract{'s' if contracts != 1 else ''}  {trader.FUTURES_SYMBOL}[/green]")
        else:
            label.update("[dim]FLAT[/dim]")

    def _refresh_next_label(self) -> None:
        label = self.query_one("#next-label", Label)
        if self._state == _RUNNING:
            secs = _countdown[0]
            if secs > 0:
                m, s = divmod(secs, 60)
                label.update(f"Next cycle in [bold]{m:02d}:{s:02d}[/bold]")
            else:
                label.update("Running cycle…")
        elif self._state == _PAUSED:
            label.update("[yellow]Paused[/yellow]")
        else:
            label.update("")

    def _refresh_position_panel(self) -> None:
        content   = self.query_one("#position-content", Static)
        daily_pnl = trader.position_cache.get("daily_pnl")
        updated   = trader.position_cache.get("updated_at")
        decision  = trader.last_decision.get("value")
        dec_time  = trader.last_decision.get("updated_at")
        portfolio = trader.portfolio_cache

        def _fmt_pnl(value):
            if value is None:
                return "[dim]—[/dim]"
            color = "green" if value >= 0 else "red"
            sign  = "+" if value >= 0 else ""
            return f"[{color}]{sign}${value:,.2f}[/{color}]"

        lines = []

        # ── Portfolio table ──────────────────────────────────────────────────
        col = f"{'Symbol':<8}  {'Type':<4}  {'Qty':>5}  {'Mkt Price':>11}  {'Avg Cost':>11}  {'P&L Open':>13}  {'P&L Daily':>13}"
        lines.append(f"[bold]{col}[/bold]")
        lines.append("─" * len(col))

        if portfolio:
            for p in portfolio:
                symbol  = p["symbol"]
                stype   = p["sec_type"]
                qty     = p["qty"]
                price_s = f"${p['market_price']:>10,.2f}" if p["market_price"] is not None else " " * 10 + "—"
                avg_s   = f"${p['avg_cost']:>10,.2f}"     if p["avg_cost"]     is not None else " " * 10 + "—"
                pnl_s   = _fmt_pnl(p["unrealized_pnl"])
                # daily P&L per position requires reqPnLSingle; show account total on the
                # first row only and blank for the rest to avoid repeating the same number.
                dpnl_s  = _fmt_pnl(daily_pnl) if p is portfolio[0] else ""
                lines.append(
                    f"[cyan]{symbol:<8}[/cyan]  {stype:<4}  {qty:>5}  {price_s}  {avg_s}  {pnl_s:>13}  {dpnl_s}"
                )
        else:
            lines.append("[dim]  No open positions.[/dim]")

        lines.append("─" * len(col))

        # ── Footer ───────────────────────────────────────────────────────────
        if decision is not None and dec_time:
            dec_label = {1: "[green]BUY[/green]", 0: "[yellow]EXIT/HOLD[/yellow]"}.get(decision, str(decision))
            lines.append(f"Last signal: {dec_label}  at {dec_time.strftime('%H:%M:%S')}")
        else:
            lines.append("Last signal: [dim]—[/dim]")

        if updated:
            lines.append(f"[dim]Updated {updated.strftime('%H:%M:%S')}[/dim]")

        content.update("\n".join(lines))

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_start(self) -> None:
        if self._state != _STOPPED:
            return
        _stop_event.clear()
        _pause_event.clear()
        self._thread = threading.Thread(target=_trading_loop, daemon=True)
        self._thread.start()
        self._apply_state(_RUNNING)

    def action_pause_resume(self) -> None:
        if self._state == _RUNNING:
            _pause_event.set()
            self._apply_state(_PAUSED)
        elif self._state == _PAUSED:
            _pause_event.clear()
            self._apply_state(_RUNNING)

    def action_stop_trading(self) -> None:
        _stop_event.set()
        _pause_event.clear()
        self._apply_state(_STOPPED)

    def action_exit_all(self) -> None:
        btn = self.query_one("#btn-exit-all", Button)
        btn.disabled = True
        btn.label = "Exiting…"

        def _run() -> None:
            try:
                trader.close_all_positions()
            finally:
                self.call_from_thread(self._reset_exit_button)

        threading.Thread(target=_run, daemon=True).start()

    def _reset_exit_button(self) -> None:
        btn = self.query_one("#btn-exit-all", Button)
        btn.disabled = False
        btn.label = "⚠  Exit All"

    def action_edit_prompt(self) -> None:
        def on_dismiss(result) -> None:
            if result is None:
                return
            trader.PROMPT = result
            ts = datetime.now().strftime("%H:%M:%S")
            self.query_one("#log", RichLog).write(
                f"[cyan]{ts}  INFO      Prompt updated.[/cyan]"
            )
        self.push_screen(PromptModal(), on_dismiss)

    def action_quit_app(self) -> None:
        _stop_event.set()
        _pause_event.clear()
        self.exit()

    # ── State management ──────────────────────────────────────────────────────

    def _apply_state(self, state: str) -> None:
        self._state   = state
        status        = self.query_one("#status-label", Label)
        btn_start     = self.query_one("#btn-start",    Button)
        btn_pause     = self.query_one("#btn-pause",    Button)
        btn_stop      = self.query_one("#btn-stop",     Button)

        if state == _RUNNING:
            status.update("[green]⬤[/green]  RUNNING")
            btn_start.disabled = True
            btn_pause.disabled = False
            btn_pause.label    = "⏸  Pause"
            btn_stop.disabled  = False
        elif state == _PAUSED:
            status.update("[yellow]⬤[/yellow]  PAUSED")
            btn_start.disabled = True
            btn_pause.disabled = False
            btn_pause.label    = "▶  Resume"
            btn_stop.disabled  = False
        else:
            status.update("[red]⬤[/red]  STOPPED")
            btn_start.disabled = False
            btn_pause.disabled = True
            btn_stop.disabled  = True


if __name__ == "__main__":
    TraderApp().run()
