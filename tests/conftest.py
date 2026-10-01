"""Unit/integration tests must stub remote HTTP; loopback fixtures remain real."""

from __future__ import annotations

import ipaddress
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest


@pytest.fixture(autouse=True)
def isolated_test_http(monkeypatch):
    original = urllib.request.OpenerDirector.open

    def open_fixture(opener, fullurl, *args, **kwargs):
        url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else str(fullurl)
        host = urlsplit(url).hostname or ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host.lower() == "localhost"
        if not local:
            raise urllib.error.URLError("Remote HTTP is disabled in tests; stub the transport")
        return original(opener, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_fixture)
