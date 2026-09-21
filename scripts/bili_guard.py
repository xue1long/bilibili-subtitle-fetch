"""Small, dependency-free guard for Bilibili browser jobs."""

import json
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


_DOWNLOAD_LIST_LOCK = threading.Lock()


@contextmanager
def download_list_lock():
    # ponytail: one-process lock; use an OS file lock only if multi-process writes are required.
    with _DOWNLOAD_LIST_LOCK:
        yield


class FailureKind:
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NO_SUBTITLE = "NO_SUBTITLE"
    BROWSER_START_FAILED = "BROWSER_START_FAILED"
    PAGE_LOAD_FAILED = "PAGE_LOAD_FAILED"
    SUBTITLE_API_FAILED = "SUBTITLE_API_FAILED"
    SUBTITLE_NOT_OBSERVED = "SUBTITLE_NOT_OBSERVED"
    UNKNOWN = "UNKNOWN"


class SessionStatus:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


class RateLimiter:
    def __init__(self, min_delay=8.0, max_delay=15.0, clock=None,
                 sleeper=None, rng=None):
        if min_delay < 0 or max_delay < min_delay:
            raise ValueError("invalid rate-limit delay range")
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._rng = rng or random.uniform
        self._last_start = None

    def wait(self):
        now = self._clock()
        if self._last_start is not None:
            delay = self._rng(self.min_delay, self.max_delay)
            remaining = self._last_start + delay - now
            if remaining > 0:
                self._sleeper(remaining)
        self._last_start = self._clock()


def _default_wall_clock():
    return datetime.now(timezone.utc)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CircuitBreaker:
    def __init__(self, state_path, threshold=2, cooldown_seconds=900,
                 clock=None, wall_clock=None):
        if threshold < 1 or cooldown_seconds < 0:
            raise ValueError("invalid circuit-breaker policy")
        self.state_path = Path(state_path)
        self.threshold = int(threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or _default_wall_clock
        self._state = "CLOSED"
        self._reason = None
        self._consecutive_rate_limits = 0
        self._opened_at = None
        self._retry_after = None
        self._load()

    def _load(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if data.get("reason") == FailureKind.RATE_LIMITED:
            self._consecutive_rate_limits = int(data.get("consecutive_rate_limits", 0))
        if data.get("state") not in ("OPEN", "HALF_OPEN") or data.get("reason") != FailureKind.RATE_LIMITED:
            return
        self._state = "OPEN"
        self._reason = FailureKind.RATE_LIMITED
        self._opened_at = self._clock()
        self._retry_after = _iso(data.get("retry_after"))

    def _persist(self):
        data = {
            "version": 1,
            "state": self._state,
            "consecutive_rate_limits": self._consecutive_rate_limits,
            "reason": self._reason,
            "opened_at": self._to_iso(self._opened_at),
            "retry_after": self._retry_after.isoformat().replace("+00:00", "Z")
            if self._retry_after else None,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_name(self.state_path.name + ".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.state_path)

    @staticmethod
    def _to_iso(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return str(value)

    def allow(self) -> bool:
        if self._state == "CLOSED":
            return True
        if self._state == "HALF_OPEN":
            return True
        if self._reason != FailureKind.RATE_LIMITED:
            return False

        mono_elapsed = self._opened_at is not None and (
            self._clock() - self._opened_at < self.cooldown_seconds
        )
        now = _iso(self._wall_clock())
        clock_moved_back = self._retry_after is not None and now is not None and now < _iso(self._retry_after)
        if mono_elapsed or clock_moved_back:
            return False
        self._state = "HALF_OPEN"
        self._persist()
        return True

    def record(self, kind: str) -> None:
        if kind == FailureKind.RATE_LIMITED:
            self._consecutive_rate_limits += 1
            self._reason = FailureKind.RATE_LIMITED
            if self._consecutive_rate_limits >= self.threshold:
                self._state = "OPEN"
                self._opened_at = self._clock()
                retry_after = _iso(self._wall_clock())
                self._retry_after = retry_after.replace(
                    microsecond=0
                ) + timedelta(seconds=self.cooldown_seconds)
            self._persist()
            return

        self._consecutive_rate_limits = 0
        if kind == FailureKind.UNKNOWN:
            return
        self.reset()

    def reset(self) -> None:
        self._state = "CLOSED"
        self._reason = None
        self._consecutive_rate_limits = 0
        self._opened_at = None
        self._retry_after = None
        self._persist()

    @property
    def state(self) -> str:
        return self._state

    @property
    def retry_after(self):
        return self._retry_after


_LOGIN_CTA_RE = re.compile(r"请先登录|登录后|登录/注册|立即登录|点我登录|登录")
_ACCOUNT_TEXT_RE = re.compile(r"个人中心|退出登录|我的大会员|用户头像")


def _driver_url(driver):
    value = getattr(driver, "current_url", None)
    if value is None:
        value = getattr(driver, "url", "")
    return value() if callable(value) else value


def _execute_script(driver, script):
    if hasattr(driver, "execute_script"):
        return driver.execute_script(script)
    if hasattr(driver, "evaluate"):
        expression = script.strip()
        if expression.startswith("return "):
            expression = expression[7:]
        if expression.endswith(";"):
            expression = expression[:-1]
        return driver.evaluate(expression)
    raise RuntimeError("unsupported browser page object")


def check_login_state(driver) -> SessionStatus:
    try:
        url = str(_driver_url(driver) or "")
        text = _execute_script(
            driver,
            "return document.body ? document.body.innerText : '';"
        ) or ""
        account_surface = _execute_script(
            driver,
            "return !!document.querySelector(\".bili-avatar, .header-avatar-wrap, "
            "#nav_user_center, [class*='avatar']\")"
        )
    except Exception:
        return SessionStatus(False, FailureKind.PAGE_LOAD_FAILED)

    if not url and not text:
        return SessionStatus(False, FailureKind.PAGE_LOAD_FAILED)
    if "/login" in url or _LOGIN_CTA_RE.search(str(text)):
        return SessionStatus(False, FailureKind.LOGIN_REQUIRED)
    if account_surface or _ACCOUNT_TEXT_RE.search(str(text)):
        return SessionStatus(True)
    return SessionStatus(False, FailureKind.LOGIN_REQUIRED)


_SUBTITLE_PROBE_DEFAULTS = {
    "request_observed": False,
    "response_status": None,
    "payload_received": False,
    "subtitle_count": 0,
    "language_count": 0,
    "hook_installed": False,
    "page_ready": False,
    "button_found": False,
    "click_dispatched": False,
    "request_count": 0,
}


def normalize_subtitle_probe(probe=None):
    normalized = dict(_SUBTITLE_PROBE_DEFAULTS)
    if probe:
        normalized.update({key: probe[key] for key in normalized if key in probe})
    return normalized


def install_subtitle_probe(driver) -> None:
    driver.execute_script(r"""
        window.__capturedSubtitleData = null;
        window.__subtitleProbe = {
            request_observed: false,
            response_status: null,
            payload_received: false,
            subtitle_count: 0,
            language_count: 0,
            hook_installed: true,
            page_ready: true,
            button_found: false,
            click_dispatched: false,
            request_count: 0
        };
        const isSubtitleUrl = (url) => url && (
            url.includes('aisubtitle.hdslb.com') || url.includes('ai_subtitle')
        );
        const recordSubtitle = (url, status, json) => {
            const probe = window.__subtitleProbe;
            if (probe.response_status !== 412 && probe.response_status !== 429) {
                probe.response_status = status;
            }
            const body = json && Array.isArray(json.body) ? json.body : [];
            if (body.length > 0 && body[0] && 'from' in body[0] &&
                'to' in body[0] && 'content' in body[0]) {
                probe.payload_received = true;
                probe.subtitle_count = body.length;
                probe.language_count = 1;
                window.__capturedSubtitleData = json;
            }
        };
        const observeRequest = () => {
            const probe = window.__subtitleProbe;
            probe.request_observed = true;
            probe.request_count += 1;
        };
        const origFetch = window.fetch;
        window.fetch = function(...args) {
            const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url);
            if (!isSubtitleUrl(url)) return origFetch.apply(this, args);
            observeRequest();
            const result = origFetch.apply(this, args);
            return result.then((response) => {
                response.clone().json().then((json) => recordSubtitle(url, response.status, json))
                    .catch(() => recordSubtitle(url, response.status, null));
                if (response.status === 412 || response.status === 429) {
                    recordSubtitle(url, response.status, null);
                }
                return response;
            });
        };
        const origOpen = window.XMLHttpRequest.prototype.open;
        window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            if (isSubtitleUrl(url)) this.__ai_url = url;
            return origOpen.call(this, method, url, ...rest);
        };
        const origSend = window.XMLHttpRequest.prototype.send;
        window.XMLHttpRequest.prototype.send = function(...args) {
            const xhr = this;
            if (xhr.__ai_url) {
                observeRequest();
                xhr.addEventListener('load', function() {
                let json = null;
                try { json = JSON.parse(xhr.responseText); } catch (e) {}
                recordSubtitle(xhr.__ai_url, xhr.status, json);
                });
            }
            return origSend.apply(this, args);
        };
    """)


def read_subtitle_probe(driver):
    return normalize_subtitle_probe(
        _execute_script(driver, "return window.__subtitleProbe || null;")
    )


def classify_failure(error=None, response_status=None, session=None,
                     subtitle_probe=None) -> str:
    if session is not None and not session.ok:
        return session.reason or FailureKind.UNKNOWN

    probe = subtitle_probe or {}
    status = response_status if response_status is not None else probe.get("response_status")
    if status in (412, 429):
        return FailureKind.RATE_LIMITED

    message = str(error or "")
    if re.search(r"driver|webdriver|browser.*start|browser.*launch|session not created|playwright|executable.*not found", message, re.I):
        return FailureKind.BROWSER_START_FAILED
    if re.search(r"page.*load|navigation|timeout", message, re.I):
        return FailureKind.PAGE_LOAD_FAILED

    if not probe.get("request_observed", False):
        return FailureKind.SUBTITLE_NOT_OBSERVED
    if probe.get("payload_received"):
        return FailureKind.UNKNOWN
    if status == 200:
        return FailureKind.NO_SUBTITLE
    if status is None:
        return FailureKind.SUBTITLE_API_FAILED
    return FailureKind.SUBTITLE_API_FAILED
