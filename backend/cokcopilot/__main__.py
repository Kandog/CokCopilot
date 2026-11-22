"""CokCopilot command line.

Personal (local) use — one user, no login, only reachable from this computer:

    python -m cokcopilot              # desktop window (pywebview) if installed, else your browser
    python -m cokcopilot --browser    # always use the browser
    python -m cokcopilot --server     # API server only
    python -m cokcopilot --dev        # API server for the Vite dev server (npm run dev)

Team server — users sign in with access keys (put it behind IIS for HTTPS):

    python -m cokcopilot serve --port 8765
    python -m cokcopilot users add "Alice" [--admin] [--email alice@company.com]
    python -m cokcopilot users list | reset-key NAME | disable NAME | enable NAME | remove NAME
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def _wait_until_up(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)


def _set_data_dir(path: str | None) -> None:
    # Must run before cokcopilot.config is imported (it reads this once).
    if path:
        os.environ["COKCOPILOT_DATA_DIR"] = str(Path(path).expanduser().resolve())


# -- local app ----------------------------------------------------------------------


def run_local(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="cokcopilot", description="Run CokCopilot on this computer.",
                                     epilog="Team server: `cokcopilot serve --help` and `cokcopilot users --help`.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true", help="open in the default browser")
    parser.add_argument("--server", action="store_true", help="run the API server only")
    parser.add_argument("--dev", action="store_true",
                        help="frontend development: API server only, accepting requests from the Vite dev server")
    parser.add_argument("--data-dir", help="data folder (default: repo data/ or %%APPDATA%%\\CokCopilot)")
    args = parser.parse_args(argv)
    _set_data_dir(args.data_dir)
    if args.dev:
        os.environ["COKCOPILOT_DEV"] = "1"
        args.server = True

    import uvicorn

    port = args.port if args.server else _free_port(args.port)
    server = uvicorn.Server(uvicorn.Config("cokcopilot.app:app", host="127.0.0.1", port=port, log_level="warning"))
    url = f"http://127.0.0.1:{port}/"

    if args.server:
        print(f"CokCopilot API on {url}")
        server.run()
        return

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_up(port)

    webview = None
    if not args.browser:
        try:
            import webview  # type: ignore[import-not-found]
        except ImportError:
            webview = None
    if webview:
        webview.create_window("CokCopilot", url, width=1280, height=840, min_size=(720, 520))
        webview.start()
        server.should_exit = True
        thread.join(timeout=10)
    else:
        print(f"CokCopilot is running at {url}  (Ctrl+C to quit)")
        webbrowser.open(url)
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
            thread.join(timeout=10)


# -- team server ------------------------------------------------------------------------


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run_serve(argv: list[str]) -> None:
    p = argparse.ArgumentParser(
        prog="cokcopilot serve",
        description="Run CokCopilot as a shared server. Users sign in with access keys. "
                    "Serves plain HTTP: put it behind IIS (or another web server) for HTTPS.")
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default 127.0.0.1: only the local IIS/proxy can reach it)")
    p.add_argument("--port", type=int, default=int(os.environ.get("HTTP_PLATFORM_PORT") or 8765),
                   help="port (default: HTTP_PLATFORM_PORT set by IIS HttpPlatformHandler, else 8765)")
    p.add_argument("--trusted-proxy", default="127.0.0.1",
                   help="IPs of proxies whose X-Forwarded-* headers are trusted (comma-separated, * for any)")
    p.add_argument("--allowed-origin", action="append", default=[],
                   help="public address users open, e.g. https://chat.example.com (needed when the proxy "
                        "doesn't pass the original Host header; repeatable)")
    p.add_argument("--data-dir", help="data folder (default: repo data/ or %%APPDATA%%\\CokCopilot)")
    args = p.parse_args(argv)
    _set_data_dir(args.data_dir)
    os.environ["COKCOPILOT_MODE"] = "server"
    if args.allowed_origin:
        os.environ["COKCOPILOT_ALLOWED_ORIGINS"] = ",".join(args.allowed_origin)

    from . import config
    from .auth import UserStore

    config.ensure_dirs()
    store = UserStore()
    if not store.list():
        user, new_key = store.add("admin", "admin")
        print("\n  No users yet, so an admin account was created.")
        print(f"  Admin access key (shown only once, keep it safe):\n\n      {new_key}\n")
        print("  Add more users in the app (Users panel) or with: python -m cokcopilot users add NAME\n",
              flush=True)

    print(f"CokCopilot server on http://{args.host}:{args.port}/   (data: {config.DATA_DIR})", flush=True)
    if not _is_loopback(args.host):
        print("Warning: listening on a network address over plain HTTP. Access keys travel unencrypted "
              "unless users reach the server through HTTPS (IIS / reverse proxy).", flush=True)

    import uvicorn

    uvicorn.run("cokcopilot.app:app", host=args.host, port=args.port, proxy_headers=True,
                forwarded_allow_ips=args.trusted_proxy, log_level="info")


# -- users ----------------------------------------------------------------------------


def run_users(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="cokcopilot users", description="Manage server users and access keys.")
    p.add_argument("--data-dir", help="data folder used by `serve`")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="create a user and print their access key")
    a.add_argument("name")
    a.add_argument("--admin", action="store_true", help="can change settings, MCP, knowledge, skills, users")
    a.add_argument("--email", default="", help="work email / UPN, for signing in with Microsoft (SSO)")
    e = sub.add_parser("set-email", help="set the email a user signs in with through Microsoft (SSO)")
    e.add_argument("name")
    e.add_argument("email")
    sub.add_parser("list", help="list users")
    for cmd, desc in (("reset-key", "issue a new key (the old one stops working)"), ("disable", "block sign-in"),
                      ("enable", "allow sign-in again"), ("remove", "delete the user (their chats stay on disk)")):
        sub.add_parser(cmd, help=desc).add_argument("name")
    args = p.parse_args(argv)
    _set_data_dir(args.data_dir)

    from . import config
    from .auth import AuthError, UserStore

    config.ensure_dirs()
    store = UserStore()
    try:
        if args.cmd == "add":
            user, key = store.add(args.name, "admin" if args.admin else "user", args.email)
            print(f"Created {user.role} \"{user.name}\". Access key (shown only once):\n\n    {key}\n")
            return
        if args.cmd == "list":
            for u in store.list():
                flag = " (disabled)" if u.disabled else ""
                print(f"{u.name:<24} {u.role:<6} key {u.key_hint}... {u.email or '-':<32}{flag}")
            if not store.list():
                print("No users yet. Add one with: python -m cokcopilot users add NAME --admin")
            return
        user = store.by_name(args.name)
        if not user:
            sys.exit(f'No user named "{args.name}".')
        if args.cmd == "reset-key":
            print(f"New access key for \"{user.name}\" (shown only once):\n\n    {store.reset_key(user.id)}\n")
        elif args.cmd == "set-email":
            store.update(user.id, email=args.email)
            print(f"{user.name} now signs in with Microsoft as {args.email.strip().lower()}")
        elif args.cmd in ("disable", "enable"):
            store.update(user.id, disabled=args.cmd == "disable")
            print(f"{user.name}: {args.cmd}d")
        elif args.cmd == "remove":
            store.remove(user.id)
            from .groups import GroupStore

            GroupStore().forget_user(user.id)
            print(f"Removed {user.name}. Their chats remain in {config.DATA_DIR / 'users' / user.id}.")
    except AuthError as e:
        sys.exit(str(e))


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        run_serve(argv[1:])
    elif argv and argv[0] == "users":
        run_users(argv[1:])
    else:
        run_local(argv)


if __name__ == "__main__":
    main()
