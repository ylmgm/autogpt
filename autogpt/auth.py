"""Identity-only Sign in with ChatGPT using a provisioned OAuth client."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

import jwt
import requests

from .storage import clear_tokens, load_metadata, load_tokens, save_metadata, save_tokens

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/oauth/authorize"
TOKEN_URL = f"{ISSUER}/oauth/token"
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
CALLBACK_HOST = "localhost"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
SCOPES = "openid profile email offline_access"
# Public OAuth client used by DBeaver's ChatGPT account integration.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


class AuthError(RuntimeError):
    pass


@dataclass
class CallbackResult:
    query: dict[str, str]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: CallbackResult | None = None
    finished: threading.Event | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if self.result is not None:
            self.result.query = query
        body = (
            "<html><meta charset='utf-8'><title>AutoGPT</title>"
            "<body style='font-family:sans-serif;margin:4em'>"
            "<h2>Sign-in complete</h2><p>You can return to AutoGPT.</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if self.finished is not None:
            self.finished.set()

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class ChatGPTAuth:
    def __init__(self) -> None:
        self.metadata = load_metadata()

    @property
    def connected(self) -> bool:
        return bool(self.metadata.get("subject") and load_tokens())

    @property
    def email(self) -> str | None:
        return self.metadata.get("email")

    @property
    def account_id(self) -> str | None:
        return self.metadata.get("account_id")

    def sign_in(self) -> str:
        """Authenticate identity only; this flow does not request model access."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect_uri = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
        finished = threading.Event()
        result = CallbackResult(query={})

        class Handler(_CallbackHandler):
            pass

        Handler.result = result
        Handler.finished = finished
        try:
            server = http.server.ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
            server.daemon_threads = True
        except OSError as exc:
            raise AuthError(f"Cannot start OAuth callback on port {CALLBACK_PORT}: {exc}") from exc
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            params = {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "originator": "autogpt",
            }
            webbrowser.open(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
            if not finished.wait(timeout=300):
                raise AuthError("Browser sign-in timed out. Please try again.")
            query = result.query
            if query.get("state") != state:
                raise AuthError("OAuth state mismatch; sign-in response rejected.")
            if query.get("error"):
                raise AuthError(f"Sign-in was not completed: {query['error']}")
            code = query.get("code")
            if not code:
                raise AuthError("Callback did not contain an authorization code.")

            response = requests.post(
                TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                },
                timeout=30,
            )
            if not response.ok:
                raise AuthError(self._format_token_error(response))
            tokens = response.json()
            identity = self._validate_id_token(tokens.get("id_token", ""), CLIENT_ID)
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            if not access_token or not refresh_token:
                raise AuthError("OAuth response did not include the Codex session tokens.")
            self.metadata = {
                "client_id": CLIENT_ID,
                "subject": identity["sub"],
                "name": identity.get("name", "ChatGPT user"),
                "email": identity.get("email"),
                "account_id": self._account_id_from_token(access_token),
            }
            save_tokens({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(time.time()) + int(tokens.get("expires_in", 3600)),
            })
            save_metadata(self.metadata)
            return self.metadata.get("email") or self.metadata["name"]
        except requests.RequestException as exc:
            raise AuthError(f"Identity token exchange failed: {exc}") from exc
        finally:
            server.shutdown()
            server.server_close()

    @staticmethod
    def _account_id_from_token(token: str) -> str | None:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            if claims.get("chatgpt_account_id"):
                return claims["chatgpt_account_id"]
            auth = claims.get("https://api.openai.com/auth", {})
            return auth.get("chatgpt_account_id")
        except (IndexError, ValueError, json.JSONDecodeError):
            return None

    def access_token(self) -> str:
        tokens = load_tokens()
        if not tokens:
            raise AuthError("Please sign in with ChatGPT first.")
        if int(tokens.get("expires_at", 0)) > int(time.time()) + 90:
            return tokens["access_token"]
        response = requests.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": tokens["refresh_token"],
            },
            timeout=30,
        )
        if not response.ok:
            clear_tokens()
            raise AuthError(self._format_token_error(response))
        renewed = response.json()
        tokens.update(renewed)
        tokens["expires_at"] = int(time.time()) + int(renewed.get("expires_in", 3600))
        save_tokens(tokens)
        return tokens["access_token"]

    def sign_out(self) -> None:
        clear_tokens()
        self.metadata = {}
        save_metadata(self.metadata)

    @staticmethod
    def _format_token_error(response: requests.Response) -> str:
        parts = [f"Identity token exchange failed: HTTP {response.status_code}"]
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if isinstance(body, dict):
            error = body.get("error")
            if error:
                parts.append(f"error={error}")
            if body.get("error_description"):
                parts.append(f"description={body['error_description']}")
        request_id = response.headers.get("x-request-id") or response.headers.get("openai-request-id")
        if request_id:
            parts.append(f"request_id={request_id}")
        return "; ".join(parts)

    @staticmethod
    def _validate_id_token(token: str, client_id: str) -> dict[str, Any]:
        if not token:
            raise AuthError("Token response did not include an ID token.")
        try:
            discovery_response = requests.get(DISCOVERY_URL, timeout=20)
            discovery_response.raise_for_status()
            discovery = discovery_response.json()
            signing_key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=client_id,
                issuer=discovery["issuer"],
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
            return claims
        except Exception as exc:
            raise AuthError(f"ID token validation failed: {exc}") from exc

