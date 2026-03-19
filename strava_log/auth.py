"""OAuth2 authentication with Strava."""

import json
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the OAuth redirect and extracts the authorization code."""

    code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            _CallbackHandler.code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization successful.</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter.")

    def log_message(self, format, *args):
        pass  # suppress default request logging


def authorize(cfg):
    """Run the full browser-based OAuth flow. Returns token dict."""
    client_id = cfg["STRAVA_CLIENT_ID"]
    client_secret = cfg["STRAVA_CLIENT_SECRET"]

    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri=http://localhost:8080/exchange_token"
        f"&response_type=code"
        f"&scope=activity:read_all"
    )

    print("Open this URL in your browser to authorize the application:\n")
    print(f"  {auth_url}\n")
    print("Waiting for authorization callback on http://localhost:8080 ...")

    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()  # handle single redirect request
    server.server_close()

    code = _CallbackHandler.code
    if not code:
        print("Error: did not receive authorization code.", file=sys.stderr)
        sys.exit(1)

    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()

    token_data = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": tokens["expires_at"],
    }

    _save_tokens(cfg, token_data)
    print("Authorization complete. Tokens saved.")
    return token_data


def refresh_tokens(cfg, token_data):
    """Refresh the access token using the stored refresh token."""
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": cfg["STRAVA_CLIENT_ID"],
            "client_secret": cfg["STRAVA_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    new = resp.json()

    token_data = {
        "access_token": new["access_token"],
        "refresh_token": new["refresh_token"],
        "expires_at": new["expires_at"],
    }

    _save_tokens(cfg, token_data)
    return token_data


def load_or_authorize(cfg, force_auth=False):
    """Load tokens from file, refreshing if needed. Authorize if no file exists."""
    token_file = cfg["STRAVA_TOKEN_FILE"]

    if force_auth:
        if os.path.exists(token_file):
            os.remove(token_file)
        return authorize(cfg)

    if not os.path.exists(token_file):
        return authorize(cfg)

    with open(token_file) as f:
        token_data = json.load(f)

    # Refresh if expired or expiring within 60 seconds
    if token_data.get("expires_at", 0) < time.time() + 60:
        print("Access token expired, refreshing...")
        try:
            token_data = refresh_tokens(cfg, token_data)
        except requests.exceptions.HTTPError:
            print(
                "Token refresh failed. Re-run with --auth to re-authenticate.",
                file=sys.stderr,
            )
            sys.exit(1)

    return token_data


def _save_tokens(cfg, token_data):
    with open(cfg["STRAVA_TOKEN_FILE"], "w") as f:
        json.dump(token_data, f, indent=2)
