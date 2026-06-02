"""Custom email backend with an env-controlled recipient sink.

When ``EMAIL_REDIRECT_TO`` is set in the environment, every outgoing
message is rewritten to address that single recipient instead of its
real ``to`` / ``cc`` / ``bcc`` list. The original recipients are
preserved in the subject (prefixed) so the redirected inbox still
shows who the mail was intended for.

When ``EMAIL_REDIRECT_TO`` is empty / unset the backend behaves
exactly like the standard SMTP backend — production stays unchanged.

Wired in via :setting:`EMAIL_BACKEND` so every send path benefits at
once: registration confirmations, log-submitted confirmations,
password resets (Django auth), bulk admin emails, and future
state-change notifications.
"""
from __future__ import annotations

import os

from django.core.mail.backends.smtp import EmailBackend


class RedirectingSMTPEmailBackend(EmailBackend):
    def send_messages(self, email_messages):
        override = (os.environ.get("EMAIL_REDIRECT_TO") or "").strip()
        if override:
            for msg in email_messages:
                original = list(msg.to or []) + list(msg.cc or []) + list(msg.bcc or [])
                msg.to = [override]
                msg.cc = []
                msg.bcc = []
                tag = ", ".join(original) if original else "(no recipients)"
                msg.subject = f"[redirect: {tag}] {msg.subject}"
        return super().send_messages(email_messages)
