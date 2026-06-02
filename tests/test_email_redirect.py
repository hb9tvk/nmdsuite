"""Tests for the recipient-redirecting SMTP backend (F0).

The backend extends Django's SMTP backend and only adds rewriting logic
before delegating to the parent's ``send_messages``. We stub the parent
call so the tests never touch a real SMTP server.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.mail import EmailMessage

from nmdsuite.email_backends import RedirectingSMTPEmailBackend


def _send_and_capture(backend: RedirectingSMTPEmailBackend, messages: list[EmailMessage]) -> list[EmailMessage]:
    """Invoke ``send_messages`` with the parent's SMTP path stubbed out so
    we can inspect the messages after they've been (potentially) rewritten."""
    sent: list[EmailMessage] = []

    def _fake_super_send(self, email_messages):
        sent.extend(email_messages)
        return len(email_messages)

    with patch(
        "django.core.mail.backends.smtp.EmailBackend.send_messages",
        _fake_super_send,
    ):
        backend.send_messages(messages)
    return sent


def _make_msg(**kwargs) -> EmailMessage:
    return EmailMessage(
        subject=kwargs.get("subject", "Hello"),
        body=kwargs.get("body", "Body"),
        from_email="nmd@uska.ch",
        to=kwargs.get("to", ["peter@example.org"]),
        cc=kwargs.get("cc", []),
        bcc=kwargs.get("bcc", []),
    )


def test_redirect_unset_passes_through(monkeypatch):
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    backend = RedirectingSMTPEmailBackend()
    msg = _make_msg(to=["peter@example.org"], subject="Welcome")
    sent = _send_and_capture(backend, [msg])
    assert sent[0].to == ["peter@example.org"]
    assert sent[0].subject == "Welcome"


def test_redirect_empty_string_passes_through(monkeypatch):
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "   ")
    backend = RedirectingSMTPEmailBackend()
    msg = _make_msg(to=["peter@example.org"], subject="Welcome")
    sent = _send_and_capture(backend, [msg])
    assert sent[0].to == ["peter@example.org"]
    assert sent[0].subject == "Welcome"


def test_redirect_rewrites_to_and_clears_cc_bcc(monkeypatch):
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "sink@test.local")
    backend = RedirectingSMTPEmailBackend()
    msg = _make_msg(
        to=["a@example.org", "b@example.org"],
        cc=["c@example.org"],
        bcc=["d@example.org"],
        subject="Welcome",
    )
    sent = _send_and_capture(backend, [msg])
    assert sent[0].to == ["sink@test.local"]
    assert sent[0].cc == []
    assert sent[0].bcc == []


def test_redirect_prefixes_subject_with_original_recipients(monkeypatch):
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "sink@test.local")
    backend = RedirectingSMTPEmailBackend()
    msg = _make_msg(
        to=["a@example.org", "b@example.org"],
        cc=["c@example.org"],
        subject="Welcome",
    )
    sent = _send_and_capture(backend, [msg])
    # All three originals listed in the prefix.
    assert sent[0].subject == "[redirect: a@example.org, b@example.org, c@example.org] Welcome"


def test_redirect_handles_empty_recipient_list(monkeypatch):
    """Defensive: a message without recipients (unusual, but possible)
    still gets the prefix so the redirected mailbox sees something."""
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "sink@test.local")
    backend = RedirectingSMTPEmailBackend()
    msg = _make_msg(to=[], subject="Welcome")
    sent = _send_and_capture(backend, [msg])
    assert sent[0].to == ["sink@test.local"]
    assert sent[0].subject == "[redirect: (no recipients)] Welcome"


def test_redirect_rewrites_each_message_independently(monkeypatch):
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "sink@test.local")
    backend = RedirectingSMTPEmailBackend()
    msgs = [
        _make_msg(to=["one@example.org"], subject="First"),
        _make_msg(to=["two@example.org"], subject="Second"),
    ]
    sent = _send_and_capture(backend, msgs)
    assert sent[0].subject == "[redirect: one@example.org] First"
    assert sent[1].subject == "[redirect: two@example.org] Second"
    assert sent[0].to == ["sink@test.local"]
    assert sent[1].to == ["sink@test.local"]
