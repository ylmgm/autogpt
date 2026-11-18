# AutoGPT — ChatGPT identity sign-in

A minimal Python desktop app that signs a user in with ChatGPT and opens a chat view. It follows DBeaver's Codex browser OAuth flow, does not request `chatgpt.tokens.use.direct`, and uses the returned session for the Codex model catalog and streaming Responses endpoint. Model access still depends on what the signed-in account can use; the app does not show the separate ChatGPT plan-consent page.

## Run on Windows

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The app opens the system browser, receives the callback on `http://localhost:1455/auth/callback`, verifies the ID token signature, issuer, audience, and expiration, then stores verified identity claims locally. Access and refresh tokens are encrypted with Windows DPAPI on Windows or stored in the OS credential store on macOS/Linux, and refreshed automatically when needed.

The client ID is public and is embedded in the app, matching DBeaver's implementation. It is not an API key or a secret.

## Project layout

- `main.py`: Tkinter sign-in and chat UI
- `autogpt/auth.py`: Codex browser OAuth with PKCE and OIDC verification
- `autogpt/storage.py`: local identity metadata and OS credential-store token storage
- `autogpt/client.py`: Codex model discovery and Responses streaming
- `requirements.txt`: Python dependencies

