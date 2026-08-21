from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RemoteSession:
    token_hash: str
    created_at: float
    expires_at: float
    client_ip: str


class PairingAuth:
    """Ephemeral, in-memory auth for the LAN web interface.

    The browser never receives the daemon token. Remote session tokens are
    stored only as SHA-256 digests and are discarded when the remote server
    exits. CSRF values are deterministically derived from the raw browser
    session token with a server-only HMAC secret, so multiple tabs can safely
    call ``/api/me`` without invalidating each other.
    """

    def __init__(
        self,
        pairing_ttl_seconds: float = 900.0,
        session_ttl_seconds: float = 43_200.0,
        max_sessions: int = 8,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._pairing_ttl = max(60.0, float(pairing_ttl_seconds))
        self._session_ttl = max(300.0, float(session_ttl_seconds))
        self._max_sessions = max(1, min(int(max_sessions), 64))
        self._lock = threading.RLock()
        self._sessions: dict[str, RemoteSession] = {}
        self._csrf_secret = secrets.token_bytes(32)
        self._pairing_code = self._new_pairing_code()
        self._pairing_expires_at = self._clock() + self._pairing_ttl

    @staticmethod
    def _new_pairing_code() -> str:
        # Eight numeric digits are easy to type on a phone while still making
        # online guessing impractical behind the request limiter.
        return f"{secrets.randbelow(100_000_000):08d}"

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _csrf_for(self, token: str) -> str:
        return hmac.new(
            self._csrf_secret,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @property
    def pairing_code(self) -> str:
        with self._lock:
            return self._pairing_code

    @property
    def pairing_expires_at(self) -> float:
        with self._lock:
            return self._pairing_expires_at

    def rotate_pairing_code(self) -> str:
        with self._lock:
            self._pairing_code = self._new_pairing_code()
            self._pairing_expires_at = self._clock() + self._pairing_ttl
            return self._pairing_code

    def _prune_unlocked(self) -> None:
        now = self._clock()
        for token_hash, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(token_hash, None)

    def pair(self, code: str, client_ip: str) -> Optional[tuple[str, str, float]]:
        now = self._clock()
        with self._lock:
            self._prune_unlocked()
            if now > self._pairing_expires_at:
                return None
            if not hmac.compare_digest(str(code).strip(), self._pairing_code):
                return None

            if len(self._sessions) >= self._max_sessions:
                # Drop the oldest session rather than allowing unbounded state.
                oldest = min(
                    self._sessions.items(), key=lambda item: item[1].created_at
                )[0]
                self._sessions.pop(oldest, None)

            token = secrets.token_urlsafe(32)
            expires_at = now + self._session_ttl
            token_hash = self._digest(token)
            self._sessions[token_hash] = RemoteSession(
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                client_ip=str(client_ip or ""),
            )
            return token, self._csrf_for(token), expires_at

    def authenticate(self, token: str) -> Optional[RemoteSession]:
        if not token:
            return None
        token_hash = self._digest(token)
        with self._lock:
            self._prune_unlocked()
            return self._sessions.get(token_hash)

    def csrf_token(self, token: str) -> Optional[str]:
        if not self.authenticate(token):
            return None
        return self._csrf_for(token)

    # Compatibility alias used by the HTTP handler. This no longer rotates and
    # therefore does not invalidate other tabs sharing the same cookie.
    refresh_csrf = csrf_token

    def validate_csrf(self, token: str, csrf: str) -> bool:
        if not csrf or not self.authenticate(token):
            return False
        expected = self._csrf_for(token)
        return hmac.compare_digest(expected, str(csrf))

    def logout(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(self._digest(token), None) is not None
