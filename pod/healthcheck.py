"""Authenticated local readiness probe; never prints the worker token."""

import json
import os
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    token = os.environ.get("POD_WORKER_TOKEN", "")
    if not token:
        return 1
    request = urllib.request.Request(
        "http://127.0.0.1:8000/v1/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        # Disable HTTP_PROXY inheritance and redirects on a local credential probe.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=8) as response:
            data = json.load(response)
        return 0 if data.get("status") == "ok" and data.get("protocol_version") == 1 else 1
    except (OSError, ValueError, urllib.error.URLError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
