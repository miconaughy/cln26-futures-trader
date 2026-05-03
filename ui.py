#!/usr/bin/env python3
"""
Trader UI — run this instead of trader.py directly.

  python3 ui.py

Controls
  ▶ Start         — begin the polling loop
  ⏸ Pause         — suspend between cycles (current cycle finishes first)
  ▶ Resume        — unpause; runs a new cycle immediately
  ⏹ Stop          — stop trading, keep the UI open
  ✎ Edit Prompt   — open the prompt editor modal
  Q               — stop trading and exit

Settings fields (editable any time; take effect on the next cycle):
  Symbol          — futures contract ticker, e.g. /CLN26
  Interval (s)    — seconds between cycles, e.g. 300
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
    log.info("Trader started.")

    while not _stop_event.is_set():
        while _pause_event.is_set():
            if _stop_event.is_set():
                return
            time.sleep(0.25)
        if _stop_event.is_set():
            break

        run_cycle()

        for remaining in range(trader.POLL_INTERVAL_SECONDS, 0, -1):
            _countdown[0] = remaining
            if _stop_event.is_set():
                return
            if _pause_event.is_set():
                _countdown[0] = 0
                break
            time.sleep(1)

    log.info("Trader stopped.")


# ── Prompt editor modal ───────────────────────────────────────────────────────

class PromptModal(ModalScreen):

    CSS = """
    PromptModal {
        align: center middle;
    }
    #dialog {
        width: 80%;
        height: 80%;
        background: $surface;
        border: thick $primary;
    }
    #dialog-title {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        padding: 0 2;
    }
    #prompt-area {
        height: 1fr;
        margin: 1 2;
        border: tall $accent;
    }
    #prompt-area:focus-within { border: tall $success; }
    #dialog-buttons {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 0 2;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("  Edit Prompt", id="dialog-title")
            yield TextArea(trader.PROMPT, id="prompt-area")
            yield Horizontal(
                Button("Save",   id="btn-save",   variant="success"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="dialog-buttons",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            text = self.query_one("#prompt-area", TextArea).text.strip()
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
    #status-label { width: 1fr; }
    #next-label   { width: 1fr; text-align: right; }

    #controls {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 1 0;
    }
    Button { min-width: 16; margin: 0 1; }

    #settings {
        height: 5;
        layout: horizontal;
        align: center middle;
        padding: 0 2;
        background: $panel;
        border: tall $primary;
    }
    .setting-group {
        width: 1fr;
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
    Input:focus  { border: tall $success; }
    Input.invalid { border: tall $error; }

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
            Label("[red]⬤[/red]  STOPPED",  id="status-label"),
            Label(trader.FUTURES_SYMBOL,     id="next-label"),
            id="status-bar",
        )
        yield Horizontal(
            Button("▶  Start",       id="btn-start",        variant="success"),
            Button("⏸  Pause",       id="btn-pause",        variant="warning", disabled=True),
            Button("⏹  Stop",        id="btn-stop",         variant="error",   disabled=True),
            Button("✎  Edit Prompt", id="btn-edit-prompt",  variant="primary"),
            id="controls",
        )
        yield Horizontal(
            Horizontal(
                Label("Symbol",       classes="setting-label"),
                Input(value=trader.FUTURES_SYMBOL,
                      placeholder="/CLN26",
                      id="input-symbol"),
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
        yield Static("  Activity Log", id="log-header")
        with Container(id="log-wrap"):
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        _configure_logging()
        self.set_interval(0.4, self._drain_log_queue)
        self.set_interval(1.0, self._refresh_next_label)

    # ── Input handlers ────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input-symbol":
            val = event.value.strip()
            if val:
                trader.FUTURES_SYMBOL = val
                event.input.remove_class("invalid")
            else:
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
        {"btn-start":       self.action_start,
         "btn-pause":       self.action_pause_resume,
         "btn-stop":        self.action_stop_trading,
         "btn-edit-prompt": self.action_edit_prompt}.get(event.button.id, lambda: None)()

    # ── Timers ────────────────────────────────────────────────────────────────

    def _drain_log_queue(self) -> None:
        log_widget = self.query_one("#log", RichLog)
        while not _log_queue.empty():
            try:
                log_widget.write(_log_queue.get_nowait())
            except queue.Empty:
                break

    def _refresh_next_label(self) -> None:
        label = self.query_one("#next-label", Label)
        if self._state == _RUNNING:
            secs = _countdown[0]
            if secs > 0:
                m, s = divmod(secs, 60)
                label.update(
                    f"Next cycle in [bold]{m:02d}:{s:02d}[/bold]"
                    f"  |  {trader.FUTURES_SYMBOL}"
                )
            else:
                label.update(f"Running cycle…  |  {trader.FUTURES_SYMBOL}")
        elif self._state == _PAUSED:
            label.update(f"[yellow]Paused[/yellow]  |  {trader.FUTURES_SYMBOL}")
        else:
            label.update(trader.FUTURES_SYMBOL)

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

    def action_edit_prompt(self) -> None:
        def on_dismiss(new_prompt: str | None) -> None:
            if new_prompt:
                trader.PROMPT = new_prompt
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
        self._state = state
        status    = self.query_one("#status-label", Label)
        btn_start = self.query_one("#btn-start",    Button)
        btn_pause = self.query_one("#btn-pause",    Button)
        btn_stop  = self.query_one("#btn-stop",     Button)

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
