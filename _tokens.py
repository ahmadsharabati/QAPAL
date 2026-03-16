"""
_tokens.py — Thread-safe token usage accumulator for QAPal.

AI clients record each call:
    from _tokens import get_token_tracker
    get_token_tracker().record(in_tok=512, out_tok=128, model="claude-sonnet-4-6", phase="plan")

Any caller can read or display a summary:
    tracker = get_token_tracker()
    log.info(tracker.format_line("plan"))     # log the summary
    tracker.reset()                           # reset between phases if desired
"""

import threading
from _log import get_logger

_log = get_logger("tokens")


class TokenTracker:
    """Accumulates token usage across all AI calls in a session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._input      = 0
        self._output     = 0
        self._cache_read = 0
        self._calls      = 0

    # ── Recording ─────────────────────────────────────────────────────

    def record(
        self,
        in_tok:     int,
        out_tok:    int,
        cache_read: int = 0,
        model:      str = "?",
        phase:      str = "",
    ) -> None:
        """Thread-safe: add one AI call's token counts to the running totals."""
        with self._lock:
            self._input      += in_tok
            self._output     += out_tok
            self._cache_read += cache_read
            self._calls      += 1
            call_num          = self._calls

        tag = f"{phase}/" if phase else ""
        cache_note = f"  cache_hit={cache_read:,}" if cache_read else ""
        _log.debug(
            "AI call #%d  %s%s  in=%d  out=%d%s",
            call_num, tag, model, in_tok, out_tok, cache_note,
        )

    def reset(self) -> None:
        """Zero all counters (call between phases to get per-phase numbers)."""
        with self._lock:
            self._input      = 0
            self._output     = 0
            self._cache_read = 0
            self._calls      = 0

    # ── Reporting ─────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a dict snapshot of current totals (thread-safe)."""
        with self._lock:
            return {
                "calls":      self._calls,
                "input":      self._input,
                "output":     self._output,
                "cache_read": self._cache_read,
                "total":      self._input + self._output,
            }

    def format_line(self, phase: str = "") -> str:
        """One-line human-readable summary, empty string if no calls recorded."""
        s = self.snapshot()
        if s["calls"] == 0:
            return ""
        prefix     = f"[{phase}] " if phase else ""
        cache_note = f"  cache_hit={s['cache_read']:,}" if s["cache_read"] else ""
        plural     = "s" if s["calls"] != 1 else ""
        return (
            f"   {prefix}tokens: {s['input']:,} in"
            f" + {s['output']:,} out"
            f" = {s['total']:,} total"
            f"  ({s['calls']} AI call{plural})"
            f"{cache_note}"
        )


# Module-level singleton — all callers share the same tracker.
_TRACKER = TokenTracker()


def get_token_tracker() -> TokenTracker:
    """Return the process-global TokenTracker instance."""
    return _TRACKER
