"""Test configuration for pytest."""
import sys
import os
from pathlib import Path

# Add project root directory to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set a high rate limit for tests to exercise middleware without causing 429 responses
# from multiple requests coming from the same IP (127.0.0.1).
# This allows testing of the rate limiting code path while preventing false failures.
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"


# ---------------------------------------------------------------------------
# v1.80.1 — webhook DNS stub (src/services/webhook_guard.py)
# ---------------------------------------------------------------------------
# Alert registration now resolves the webhook host (SSRF guard). Tests must never hit
# real DNS, so every test gets a dict-backed resolver: IP literals pass through, names
# set in the mapping return their list (webhook_dns["rebind.test"] = ["127.0.0.1"]),
# names in webhook_dns.unresolvable fail like NXDOMAIN, and every other name resolves
# to one public address — which keeps example.com / a.com / *.example fixtures offline.

import pytest  # noqa: E402

_PUBLIC_TEST_ADDRESS = "93.184.216.34"


class _WebhookDNSStub(dict):
    def __init__(self):
        super().__init__()
        self.unresolvable = set()
        self.calls = []

    async def resolve(self, host, port):
        from src.services.webhook_guard import REASON_UNRESOLVABLE, WebhookURLRejected
        self.calls.append((host, port))
        key = host.lower().rstrip(".")
        if key in self.unresolvable:
            raise WebhookURLRejected(REASON_UNRESOLVABLE)
        if key in self:
            value = self[key]
            return list(value() if callable(value) else value)
        return [_PUBLIC_TEST_ADDRESS]


@pytest.fixture(autouse=True)
def webhook_dns(monkeypatch):
    """Autouse: stub webhook_guard.resolve_host for every test (no real DNS)."""
    from src.services import webhook_guard
    stub = _WebhookDNSStub()
    monkeypatch.setattr(webhook_guard, "resolve_host", stub.resolve)
    return stub
