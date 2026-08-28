"""Managed, stdlib-only launchers and per-process HTTP-to-SOCKS5 bridge."""
from __future__ import annotations

import argparse
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
import ipaddress
from pathlib import Path

from setup_external_updates import advisory, cache_fresh, load_cache
from setup_inventory import ExternalCliSpec, external_cli_inventory

SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 1080


def socks_address() -> tuple[str, int]:
    return (
        os.environ.get("AGENT_TOOLCHAIN_SOCKS_HOST", SOCKS_HOST),
        int(os.environ.get("AGENT_TOOLCHAIN_SOCKS_PORT", str(SOCKS_PORT))),
    )


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("SOCKS5 connection closed during handshake")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def socks5_preflight(host: str = SOCKS_HOST, port: int = SOCKS_PORT, timeout: float = 1.5) -> None:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(b"\x05\x01\x00")
        if _recv_exact(sock, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5 proxy rejected no-auth handshake")


def _socks_connect(host: str, port: int, timeout: float = 5.0) -> socket.socket:
    sock = socket.create_connection(socks_address(), timeout=timeout)
    try:
        sock.sendall(b"\x05\x01\x00")
        if _recv_exact(sock, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5 authentication failed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and address.version == 4:
            destination = b"\x01" + address.packed
        elif address and address.version == 6:
            destination = b"\x04" + address.packed
        else:
            encoded = host.encode("idna")
            if len(encoded) > 255:
                raise ValueError("SOCKS5 hostname is too long")
            destination = b"\x03" + bytes([len(encoded)]) + encoded
        request = b"\x05\x01\x00" + destination + port.to_bytes(2, "big")
        sock.sendall(request)
        head = _recv_exact(sock, 4)
        if head[1] != 0:
            raise ConnectionError(f"SOCKS5 CONNECT failed: {head[1]}")
        atyp = head[3]
        if atyp == 1:
            size = 4
        elif atyp == 3:
            size = _recv_exact(sock, 1)[0]
        elif atyp == 4:
            size = 16
        else:
            raise ConnectionError("invalid SOCKS5 address type")
        _recv_exact(sock, size)
        _recv_exact(sock, 2)
        sock.settimeout(None)
        return sock
    except Exception:
        sock.close()
        raise


def _relay(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], None)
            if not readable:
                continue
            for source in readable:
                data = source.recv(64 * 1024)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    finally:
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(10)
        header = b""
        while b"\r\n\r\n" not in header and len(header) <= 64 * 1024:
            part = self.request.recv(4096)
            if not part:
                return
            header += part
        if b"\r\n\r\n" not in header:
            return
        raw, body = header.split(b"\r\n\r\n", 1)
        lines = raw.split(b"\r\n")
        first = lines[0].decode("latin1").split(" ", 2)
        if len(first) != 3:
            return
        method, target, version = first
        content_length = 0
        for line in lines[1:]:
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    return
        while len(body) < content_length:
            part = self.request.recv(min(64 * 1024, content_length - len(body)))
            if not part:
                return
            body += part
        if method.upper() == "CONNECT":
            host, sep, port_text = target.rpartition(":")
            port = int(port_text) if sep else 443
            upstream = _socks_connect(host, port)
            try:
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if body:
                    upstream.sendall(body)
                _relay(self.request, upstream)
            finally:
                upstream.close()
            return
        from urllib.parse import urlsplit
        parsed = urlsplit(target)
        host = parsed.hostname
        if not host:
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        out = [f"{method} {path} {version}".encode("latin1")]
        for line in lines[1:]:
            if not line.lower().startswith((b"proxy-connection:", b"proxy-authorization:")):
                out.append(line)
        upstream = _socks_connect(host, port)
        try:
            upstream.sendall(b"\r\n".join(out) + b"\r\n\r\n" + body)
            _relay(self.request, upstream)
        finally:
            upstream.close()


class HttpSocksBridge:
    def __init__(self) -> None:
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ProxyHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="proxy-tools-bridge", daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        return self.server.server_address

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def launch(command: str, argv: list[str]) -> int:
    inventory = external_cli_inventory(ExternalCliSpec(command, command.title()))
    if not inventory.active:
        print(f"{command}: no executable found", file=sys.stderr)
        return 127
    record = load_cache().get("tools", {}).get(command)
    if record and cache_fresh(record):
        message = advisory(inventory, record)
        if message:
            print(message, file=sys.stderr)
    else:
        print(
            f"{command}: update advisory cache missing/stale; run toolchainctl updates refresh",
            file=sys.stderr,
        )
    try:
        socks5_preflight(*socks_address())
    except (OSError, ConnectionError, ValueError) as exc:
        print(f"SOCKS5 preflight failed: {exc}", file=sys.stderr)
        return 78
    bridge = HttpSocksBridge()
    bridge.start()
    env = os.environ.copy()
    proxy = f"http://{bridge.address[0]}:{bridge.address[1]}"
    socks_host, socks_port = socks_address()
    env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": f"socks5://{socks_host}:{socks_port}", "NO_PROXY": "localhost,127.0.0.1,::1"})
    env.update({"http_proxy": proxy, "https_proxy": proxy, "all_proxy": env["ALL_PROXY"], "no_proxy": env["NO_PROXY"]})
    try:
        child = subprocess.Popen([str(inventory.active.canonical_path), *argv], cwd=os.getcwd(), env=env)
        return child.wait()
    finally:
        bridge.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("opencode", "codex"))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.args == ["--health"]:
        print(f"{args.command}-proxied: launch {args.command} through the managed SOCKS5 proxy")
        return 0
    return launch(args.command, args.args)


if __name__ == "__main__":
    raise SystemExit(main())
