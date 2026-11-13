"""Cookie-aware HTTP client for the portal (standard library only)."""

import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request


class PortalSession:
    """Keeps the session cookie across requests and counts HTTP calls."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.requests = 0
        self.cookiejar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )

    def has_session_cookie(self):
        return any(c.name == "portal_session" for c in self.cookiejar)

    def get(self, path):
        return self._open(urllib.request.Request(self.base_url + path, method="GET"))

    def post(self, path, fields):
        body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return self._open(request)

    def _open(self, request):
        self.requests += 1
        try:
            with self._opener.open(request) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

