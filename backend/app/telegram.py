"""Telegram bot for alerts: one shared bot, long polling inside the app (no webhook, no open port).

Linking: the site creates a one-time code and opens t.me/<bot>?start=<code>; the user taps Start, the bot gets
"/start <code>" and links that private chat to the account. /stop unlinks. Messages go out through one paced sender
(Telegram allows ~30 messages/s per bot and ~1/s per chat); alerts have their own queue and go first, every chat
has its own pacing, and flood control on one chat (429) parks only that chat — a user spamming the bot can neither
delay other people's alerts nor make the bot spam back (replies are rate-limited per chat and dropped first). Enabled only when TELEGRAM_BOT_TOKEN is set (one instance per bot: long polling is exclusive).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("lv.telegram")

API = "https://api.telegram.org"
SEND_INTERVAL_S = 1 / 20      # all chats together (Telegram: ~30 messages/s per bot)
PER_CHAT_S = 1.1              # one chat (Telegram: ~1 message/s)
REPLY_EVERY_S = 3.0           # a chat that writes to the bot more often gets no reply at all
ALERTS_MAX, REPLIES_MAX = 5000, 200
CODE_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")

MSG = {
    "linked": {
        "en": "✅ Connected. FibTrader View alerts will come to this chat.\nSend /stop to disconnect.",
        "ru": "✅ Подключено. Алерты FibTrader View будут приходить в этот чат.\nОтключить: /stop",
    },
    "expired": {
        "en": "This link has expired. Open Alerts on the site and press “Connect Telegram” again.",
        "ru": "Ссылка устарела. Откройте «Алерты» на сайте и нажмите «Подключить Telegram» ещё раз.",
    },
    "help": {
        "en": "This bot sends price alerts from FibTrader View. To connect it, open Alerts on the site and press "
              "“Connect Telegram”.",
        "ru": "Этот бот присылает алерты по ценам из FibTrader View. Чтобы подключить его, откройте «Алерты» на сайте "
              "и нажмите «Подключить Telegram».",
    },
    "unlinked": {
        "en": "Disconnected. Alerts will no longer come here.",
        "ru": "Отключено. Алерты сюда больше не придут.",
    },
}


class TgError(Exception):
    def __init__(self, code: int | None, description: str, retry_after: float | None = None):
        super().__init__(f"{code} {description}")
        self.code, self.retry_after = code, retry_after


DbCall = Callable[..., Awaitable]


class TelegramBot:
    def __init__(self, token: str, db_call: DbCall, client: httpx.AsyncClient | None = None):
        self._token = token
        self._db = db_call  # async (method_name, *args) on UserDB in its thread pool
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10, read=65))
        self.username: str | None = None
        self._alerts: deque[tuple[int, str]] = deque()
        self._replies: deque[tuple[int, str]] = deque()
        self._next_ok: dict[int, float] = {}   # chat -> monotonic time it may get the next message
        self._last_in: dict[int, float] = {}   # chat -> when the bot last answered it
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        try:
            me = await self._call("getMe")
            self.username = me.get("username")
        except Exception as exc:  # noqa: BLE001 — the site works without the bot
            log.warning("telegram bot unavailable: %s", exc)
            return
        self._tasks = [asyncio.create_task(self._poll(), name="tg-poll"), asyncio.create_task(self._sender(), name="tg-send")]
        log.info("telegram bot @%s up", self.username)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass
        await self._client.aclose()

    @property
    def available(self) -> bool:
        return bool(self.username) and bool(self._tasks)

    def link_url(self, code: str) -> str:
        return f"https://t.me/{self.username}?start={code}"

    # --------------------------------------------------------------- sending
    def send_alert(self, chat_id: int, text: str) -> bool:
        return self._put(self._alerts, ALERTS_MAX, chat_id, text)

    def _reply(self, chat_id: int, text: str) -> None:
        self._put(self._replies, REPLIES_MAX, chat_id, text)

    def _put(self, q: deque, maxlen: int, chat_id: int, text: str) -> bool:
        if len(q) >= maxlen:
            log.warning("telegram queue full, message to %s dropped", chat_id)
            return False
        q.append((chat_id, text[:4000]))
        self._wake.set()
        return True

    def _pick(self, now: float) -> tuple[deque | None, int, float]:
        """First message whose chat may be messaged now (alerts first). Else: (None, -1, seconds to wait)."""
        wait = 3600.0
        for q in (self._alerts, self._replies):
            for i, (chat_id, _text) in enumerate(q):
                ready = self._next_ok.get(chat_id, 0.0)
                if ready <= now:
                    return q, i, 0.0
                wait = min(wait, ready - now)
        return None, -1, wait

    async def _sender(self) -> None:
        while True:
            now = time.monotonic()
            q, i, wait = self._pick(now)
            if q is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, wait))
                except TimeoutError:
                    pass
                continue
            chat_id, text = q[i]
            del q[i]
            retry_after = await self._deliver(chat_id, text)
            self._next_ok[chat_id] = time.monotonic() + max(PER_CHAT_S, retry_after or 0.0)
            if retry_after and q is self._alerts:
                q.appendleft((chat_id, text))  # flood control on this chat: it waits, the others go on
            if len(self._next_ok) > 20_000:
                cut = time.monotonic()
                self._next_ok = {k: v for k, v in self._next_ok.items() if v > cut}
            await asyncio.sleep(SEND_INTERVAL_S)

    async def _deliver(self, chat_id: int, text: str) -> float | None:
        """Send once. Returns seconds to wait before retrying this chat (429), else None (sent or given up)."""
        try:
            await self._call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
        except TgError as exc:
            if exc.code == 429:
                return float(min(exc.retry_after or 5, 300))
            if exc.code == 403:  # the user blocked the bot or deleted the chat: stop sending there
                await self._db("tg_unlink_chat", chat_id)
            log.info("telegram send to %s failed: %s", chat_id, exc)
        except httpx.HTTPError as exc:
            log.info("telegram send to %s: %s", chat_id, type(exc).__name__)
            return 5.0
        return None

    # --------------------------------------------------------------- polling
    async def _poll(self) -> None:
        offset, backoff = 0, 1.0
        while True:
            try:
                updates = await self._call("getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.info("telegram poll: %s", type(exc).__name__)  # never the URL: it contains the token
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for u in updates:
                offset = max(offset, int(u.get("update_id", 0)) + 1)
                try:
                    await self.on_message(u.get("message") or {})
                except Exception:  # noqa: BLE001
                    log.exception("telegram update failed")

    async def on_message(self, m: dict) -> None:
        chat = m.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            return
        chat_id = chat["id"]
        now = time.monotonic()
        if now - self._last_in.get(chat_id, 0.0) < REPLY_EVERY_S:
            return  # flooding the bot: ignore (no reply, no database work)
        self._last_in[chat_id] = now
        if len(self._last_in) > 20_000:
            self._last_in = {k: v for k, v in self._last_in.items() if now - v < REPLY_EVERY_S}
        text = (m.get("text") or "").strip()
        lang = "ru" if str((m.get("from") or {}).get("language_code") or "").startswith("ru") else "en"
        if text.startswith("/start"):
            code = text[len("/start"):].strip()
            if not code:
                self._reply(chat_id, MSG["help"][lang])
                return
            res = await self._db("tg_claim", code, chat_id) if CODE_RE.fullmatch(code) else None
            self._reply(chat_id, MSG["linked"][res[1]] if res else MSG["expired"][lang])
        elif text == "/stop":
            await self._db("tg_unlink_chat", chat_id)
            self._reply(chat_id, MSG["unlinked"][lang])
        else:
            self._reply(chat_id, MSG["help"][lang])

    # ------------------------------------------------------------------ http
    async def _call(self, method: str, **params) -> dict | list:
        r = await self._client.post(f"{API}/bot{self._token}/{method}", json=params)
        try:
            j = r.json()
        except ValueError as exc:
            raise TgError(r.status_code, "bad response") from exc
        if not j.get("ok"):
            raise TgError(j.get("error_code"), str(j.get("description", "")),
                          (j.get("parameters") or {}).get("retry_after"))
        return j["result"]
