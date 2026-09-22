"""Gmail API client (send-only) -- used to actually deliver collections /
dunning emails. Same OAuth refresh-token pattern as integrations/qbo.py:
exchange a long-lived refresh token for a short-lived access token, refresh
transparently on expiry or a 401.

Needs GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, and
GMAIL_SENDER_EMAIL. The refresh token must have been issued for the scope
https://www.googleapis.com/auth/gmail.send -- nothing broader is requested
or needed (this client can only send, never read a mailbox).
"""

from __future__ import annotations

import base64
import logging
import time
from email.message import EmailMessage

from config import Settings, update_dotenv_value
from integrations import _http

log = logging.getLogger("gmail")

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._access: str | None = None
        self._expires_at = 0.0
        self._refresh = settings.gmail_refresh_token

    # -- auth --------------------------------------------------------- #
    def _ensure_token(self, force: bool = False) -> None:
        if not force and self._access and time.time() < self._expires_at - 60:
            return
        if not (self._refresh and self.s.gmail_client_id and self.s.gmail_client_secret):
            raise RuntimeError("Gmail refresh credentials not configured")
        res = _http.request(
            "POST",
            _OAUTH_TOKEN_URL,
            form_body={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh,
                "client_id": self.s.gmail_client_id,
                "client_secret": self.s.gmail_client_secret,
            },
        )
        self._access = res["access_token"]
        self._expires_at = time.time() + int(res.get("expires_in", 3600))
        # Google doesn't usually rotate refresh tokens on every use the way
        # QBO does, but it can happen (e.g. a security event) -- handle it
        # the same way so it can never silently go stale.
        new_refresh = res.get("refresh_token")
        if new_refresh and new_refresh != self._refresh:
            self._refresh = new_refresh
            if update_dotenv_value("GMAIL_REFRESH_TOKEN", new_refresh):
                log.warning("Gmail refresh token rotated — new value saved to .env automatically")
            else:
                log.warning("Gmail refresh token rotated but no .env file found to update — set GMAIL_REFRESH_TOKEN manually")

    def _h(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access}", "Content-Type": "application/json"}

    # -- send ---------------------------------------------------------- #
    def send_email(self, to: str, subject: str, body_text: str) -> str:
        """Sends a plain-text email from the configured sender. Returns the
        Gmail message id on success. Never called automatically by this
        codebase -- only in direct response to a specific user action, and
        raises on failure rather than swallowing it, so a caller can't
        report success that didn't happen."""
        msg = EmailMessage()
        msg["To"] = to
        msg["From"] = self.s.gmail_sender_email
        msg["Subject"] = subject
        msg.set_content(body_text)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        try:
            res = _http.request("POST", _SEND_URL, headers=self._h(), json_body={"raw": raw})
        except _http.HttpError as e:
            if e.status == 401:
                self._ensure_token(force=True)
                res = _http.request("POST", _SEND_URL, headers=self._h(), json_body={"raw": raw})
            else:
                raise
        return res.get("id", "")
