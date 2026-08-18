"""CLI for the AURON MCP operator.

Commands:
    login     Authenticate against the real AURON API and persist auth cookies.
    status    Verify the current session and print the resolved identity.
    clean     Remove the persisted session (logout locally).

The tokens AURON issues are httpOnly cookies, so this CLI uses a cookie-aware
HTTP client and stores the cookies in a JSON file (AURON_MCP_SESSION_PATH),
exactly as AURON's own frontend treats its session.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx2 as httpx

from .config import settings

LOGIN_DEFAULT_PATH = "~/.auron-mcp/session.json"


def _session_path() -> Path:
    return Path(os.environ.get("AURON_MCP_SESSION_PATH", LOGIN_DEFAULT_PATH)).expanduser()


def _load_cookies() -> list[dict]:
    path = _session_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("cookies", [])


def load_session_cookies() -> list[dict]:
    """Load persisted AURON auth cookies (from `auron-manage login`)."""
    return _load_cookies()


def _save_cookies(cookies: list[dict]) -> None:
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"base_url": settings.api_base_url, "cookies": cookies}, indent=2),
        encoding="utf-8",
    )


def cmd_login(args) -> int:
    email = args.email or input("Email: ").strip()
    password = args.password or _prompt_password()
    if not settings.api_base_url:
        print("AURON_API_BASE_URL is required (e.g. http://localhost:8000)")
        return 2
    with httpx.Client(base_url=settings.api_base_url, timeout=30.0) as client:
        body: dict = {"email": email, "password": password}
        if args.subdomain:
            body["tenant_subdomain"] = args.subdomain
        response = client.post("/api/auth/login/", json=body)
        if response.status_code == 200:
            cookies = [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in client.cookies.jar
            ]
            _save_cookies(cookies)
            payload = response.json()
            user = payload.get("user", {})
            print(
                f"Login OK: {user.get('email')} (role={user.get('role')}) "
                f"tenant={payload.get('tenant', {}).get('subdomain')}"
            )
            return 0
        if response.status_code == 429:
            print("Rate limited by AURON login endpoint; try again later.")
            return 1
        print(f"Login failed (HTTP {response.status_code}). Check credentials/subdomain.")
        return 1


def cmd_status(args) -> int:
    if not settings.api_base_url:
        print("AURON_API_BASE_URL is required.")
        return 2
    cookies = _load_cookies()
    if not cookies:
        print("No session found. Run: auron-manage login")
        return 1
    with httpx.Client(base_url=settings.api_base_url, timeout=30.0) as client:
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path"))
        try:
            response = client.get("/api/auth/verify/")
        except httpx.RequestError:
            print("Could not reach AURON API.")
            return 1
        if response.status_code == 200:
            user = response.json().get("user", {})
            print(
                f"Authenticated: {user.get('email')} (role={user.get('role')}) "
                f"branch_id={user.get('branch_id')}"
            )
            return 0
        print(f"Session invalid (HTTP {response.status_code}). Run: auron-manage login")
        return 1


def cmd_clean(args) -> int:
    path = _session_path()
    if path.exists():
        path.unlink()
        print(f"Session removed: {path}")
    else:
        print("No session to remove.")
    return 0


def _prompt_password() -> str:
    import getpass

    return getpass.getpass("Password: ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auron-manage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Authenticate against the AURON API")
    login.add_argument("--email")
    login.add_argument("--password")
    login.add_argument("--subdomain", help="Tenant subdomain (required for non-superusers)")
    login.set_defaults(func=cmd_login)

    status = sub.add_parser("status", help="Verify the current session")
    status.set_defaults(func=cmd_status)

    clean = sub.add_parser("clean", help="Remove the persisted session")
    clean.set_defaults(func=cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
