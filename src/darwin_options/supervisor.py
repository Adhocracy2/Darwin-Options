"""The Supervisor: a chat assistant in the dashboard that answers questions about the system.

It runs on Claude (Anthropic API) with read-only tools that look up the live state: status,
positions, risk, the last scan, recent events, settings and saved market data. It can't place,
close or change anything; those stay with the trader and the owner.

Needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in the environment, e.g. ~/darwin/shared/.env.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # re-runs a declined request on another model
MAX_HISTORY = 20  # messages kept from the conversation
MAX_CHARS = 4000  # per message from the browser

# Frozen text (no timestamps or live values) so it caches; live facts come from the tools.
SYSTEM = """You are the Supervisor of Darwin Options, an automated options-trading system the owner runs \
on their own Ubuntu machine (AI1) against an Interactive Brokers (IBKR) paper account. You sit in the \
dashboard's 3D office, where Darwin's parts are shown as teams at desks, and the owner talks to you there.

How Darwin works, in pipeline order:
- Market Data: pulls stock and option prices through IB Gateway (IBKR's desktop app on AI1, port 4002), \
which talks to IBKR's servers. Every price is also saved for replays.
- Scanner: builds and scores trade ideas for each enabled strategy (credit spreads, iron condors, iron \
butterflies, calendars, LEAPS calls; "0DTE" means a strategy set to options expiring today).
- Risk Desk: approves or blocks each idea. The paper account simulates a $10,000 account: 1-5% of equity \
at risk per trade (2% until 30 trades have closed), up to 10 positions, 30% total at risk, 2 per ticker, \
30% cap on the correlated SPY/QQQ/IWM/DIA group, drawdown brakes at 15% and 30%, 6% daily loss stop.
- Trading: places approved trades as one combo order and works the fill.
- Portfolio: the open positions.
- Exits & Rolls: closes positions by rule: take profit (usually 50% of the credit), stop loss, a threatened \
short strike, the planned time exit (days before expiry), and at most one roll at the time exit.
- Review: learns from closed trades; suggests changes only with 20+ trades per group.
- The Engine runs that cycle every few minutes during US market hours (9:30-16:00 New York).
- News Desk: set up for market news and earnings/ex-dividend dates, not tasked yet.
- Technology: keeps the software, the AI1 server, the home router and internet link, IB Gateway and the \
link to IBKR running. The dashboard pings google.com (and the router if that fails) every 30 seconds.

Hard rules the system never breaks: never a naked short option; every trade's max loss is known and capped \
before it is placed; short options are closed before they can be assigned; live trading needs two separate \
switches; a stop loss is never rolled.

Market data is 15 minutes delayed until the owner subscribes to live options data (OPRA). Delayed quotes make \
fills less likely and block same-day (0DTE) trades.

How to answer:
- The owner reads on a phone, often between other things. Lead with the answer in one or two plain sentences, \
then only the detail that helps. Short paragraphs or a few bullets; no tables unless asked.
- They are not a professional trader or developer. Explain an options term in a few words the first time it \
matters.
- Look things up with your tools before answering anything about the current state; never guess numbers. Say \
when a figure is delayed, simulated, an estimate or a model output, and how old the data is if it matters.
- You can only look. If the owner asks you to trade, close a position or change a setting, say what they'd \
change and where (the dashboard's Positions or Strategies tabs), and don't claim to have done it.
- If a tool fails or IB Gateway is offline, say so plainly and work with what you have."""


class SupervisorUnavailable(Exception):
    """The Supervisor can't run: no API key, or the SDK isn't installed."""


def _dump(x) -> str:
    out = json.dumps(x, default=str, separators=(",", ":"))
    return out if len(out) <= 30_000 else out[:30_000] + "...(cut)"


def clean_history(history: list[dict]) -> list[dict]:
    """Text-only user/assistant turns from the browser, newest MAX_HISTORY, starting with the owner."""
    msgs = [{"role": m["role"], "content": str(m.get("content", ""))[:MAX_CHARS]}
            for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and str(m.get("content", "")).strip()]
    msgs = msgs[-MAX_HISTORY:]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message must be from you")
    return msgs


class Supervisor:
    """`ctx` maps tool names to zero/one-argument functions from the dashboard (see api/app.py)."""

    def __init__(self, ctx: dict[str, Callable], model: str = MODEL, effort: str = "medium", max_rounds: int = 8):
        try:
            import anthropic
        except ImportError as e:
            raise SupervisorUnavailable("The anthropic Python package isn't installed.") from e
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SupervisorUnavailable(
                "No Claude API key yet. Add ANTHROPIC_API_KEY=... to ~/darwin/shared/.env, then restart darwin-web.")
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=120.0, max_retries=2)
        self.model, self.effort, self.max_rounds = model, effort, max_rounds
        self.tools = self._tools(ctx)

    def _tools(self, ctx: dict[str, Callable]) -> list:
        from anthropic import beta_tool

        def safe(name: str, *args) -> str:
            try:
                return _dump(ctx[name](*args))
            except Exception as e:  # report it to the model instead of failing the chat
                log.warning("supervisor tool %s failed: %s", name, e)
                return _dump({"error": f"{type(e).__name__}: {e}"})

        @beta_tool
        def get_overview() -> str:
            """Current state in one call: time, mode, market open or closed, IB Gateway and its link to IBKR, \
tickers, whether new entries are on, account equity and money at risk, open positions with P&L, the last \
scan's counts and top block reasons, and overall performance. Start here."""
            return safe("overview")

        @beta_tool
        def get_positions(status: str = "open") -> str:
            """Positions with legs, entry prices, max loss and P&L.

            Args:
                status: "open", "closed" or "all".
            """
            return safe("positions", status if status in ("open", "closed", "all") else "open")

        @beta_tool
        def get_position_detail(position_id: str) -> str:
            """One open position in depth: what was received, the cost to close now, each exit rule's \
trigger level, and a projected outcome (chance of profit, average result) from a price-path simulation.

            Args:
                position_id: The position's id, e.g. "b3cd2f6e2685" (from get_positions).
            """
            return safe("position_detail", position_id)

        @beta_tool
        def get_last_scan(limit: int = 10) -> str:
            """The latest scan: when it ran, how many ideas, how many passed the risk checks, the most \
common block reasons, and the best-scoring ideas with credit, max loss, chance of profit and expected value.

            Args:
                limit: How many top ideas to include (1-30).
            """
            return safe("scan", max(1, min(int(limit), 30)))

        @beta_tool
        def get_recent_events(limit: int = 20) -> str:
            """The trading journal, newest first: trades opened and closed, unfilled orders, rolls, \
errors, settings changes.

            Args:
                limit: How many events (1-100).
            """
            return safe("events", max(1, min(int(limit), 100)))

        @beta_tool
        def get_risk() -> str:
            """Risk state: equity, drawdown, today's change, per-trade cap and floor, money at risk vs the \
limit, probation status and any reasons trading is reduced or halted."""
            return safe("risk")

        @beta_tool
        def get_settings() -> str:
            """Strategies (on/off, entry settings such as days to expiry and short delta, exit rules), the \
tickers in use, and whether new entries are allowed."""
            return safe("settings")

        @beta_tool
        def get_market_data() -> str:
            """What price history has been saved for replays: quote counts, date range, tickers, size, and \
when the last price was saved."""
            return safe("market_data")

        return [get_overview, get_positions, get_position_detail, get_last_scan, get_recent_events,
                get_risk, get_settings, get_market_data]

    def chat(self, history: list[dict]) -> dict:
        """Answer the newest message. Returns {reply, tools_used}."""
        messages = clean_history(history)
        a = self.anthropic
        try:
            runner = self.client.beta.messages.tool_runner(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                tools=self.tools,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            used: list[str] = []
            last = None
            for rounds, message in enumerate(runner, start=1):
                last = message
                used += [b.name for b in message.content if b.type == "tool_use"]
                if rounds >= self.max_rounds and message.stop_reason == "tool_use":
                    break
        except a.AuthenticationError:
            raise SupervisorUnavailable("The Claude API key was rejected. Check ANTHROPIC_API_KEY in ~/darwin/shared/.env.")
        except a.PermissionDeniedError as e:
            raise SupervisorUnavailable(f"The Claude API key isn't allowed to do this: {e.message}")
        except a.RateLimitError:
            return {"reply": "I'm being rate-limited by the Claude API. Try again in a minute.", "tools_used": []}
        except a.APIStatusError as e:
            log.warning("supervisor API error %s: %s", e.status_code, e.message)
            return {"reply": f"The Claude API returned an error ({e.status_code}). Try again shortly.", "tools_used": []}
        except a.APIConnectionError:
            return {"reply": "I couldn't reach the Claude API. Is AI1 online?", "tools_used": []}

        if last is None:
            return {"reply": "I didn't get an answer back. Try again.", "tools_used": used}
        if last.stop_reason == "refusal":
            return {"reply": "I can't help with that one.", "tools_used": used}
        reply = "\n\n".join(b.text for b in last.content if b.type == "text").strip()
        if last.stop_reason == "tool_use":
            reply = (reply + "\n\n" if reply else "") + "(I stopped after several lookups. Ask a narrower question?)"
        elif last.stop_reason == "max_tokens":
            reply += "\n\n(My answer was cut off.)"
        return {"reply": reply or "I don't have an answer for that.", "tools_used": used}
