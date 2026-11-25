#!/usr/bin/env python3
"""Agent problem-report CLI for Better Xiaomi Cloud Map Extractor.

Python 3 standard library only (no dependencies), strictly read-only against Home Assistant.

Usage:
  HA_URL=http://homeassistant.local:8123 HA_TOKEN=... python3 scripts/agent-report/cli.py collect --out report.json
  python3 scripts/agent-report/cli.py collect --raw snapshot.json --out report.json
  python3 scripts/agent-report/cli.py render report.json > issue.md   # prints title to stderr
  python3 scripts/agent-report/cli.py check issue.md                  # validates an agent report

See .agents/skills/report-problem/SKILL.md for the full procedure.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Allow running directly as `python3 scripts/agent-report/cli.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import (  # noqa: E402
    DOMAIN,
    DOMAINS,
    ORIGINAL_DOMAIN,
    build_report,
    issue_title,
    missing_fields,
    render_issue_body,
    review_issue,
)


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------------------------
# Minimal stdlib RFC 6455 WebSocket client for Home Assistant /api/websocket
# ---------------------------------------------------------------------------------------------


class _HAWebSocket:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        parsed = urllib.parse.urlparse(base_url.rstrip("/"))
        if parsed.scheme not in ("http", "https", "ws", "wss"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
        use_tls = parsed.scheme in ("https", "wss")
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid Home Assistant URL: {base_url}")
        port = parsed.port or (443 if use_tls else 80)
        ws_path = (parsed.path.rstrip("/") or "") + "/api/websocket"

        raw_sock = socket.create_connection((host, port), timeout=timeout)
        if use_tls:
            ctx = ssl.create_default_context()
            self._sock: socket.socket = ctx.wrap_socket(raw_sock, server_hostname=host)
        else:
            self._sock = raw_sock
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._next_id = 1

        self._handshake(host, port, use_tls, ws_path)
        self._authenticate(token)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("Home Assistant closed the WebSocket connection")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _handshake(self, host: str, port: int, use_tls: bool, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_header = host if (port == 443 and use_tls) or (port == 80 and not use_tls) else f"{host}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("No WebSocket handshake response from Home Assistant")
            self._buf.extend(chunk)
        header_end = self._buf.index(b"\r\n\r\n")
        header_bytes = bytes(self._buf[:header_end])
        del self._buf[: header_end + 4]
        status_line = header_bytes.split(b"\r\n", 1)[0].decode("latin1")
        if " 101 " not in status_line:
            raise ConnectionError(f"WebSocket upgrade failed: {status_line}")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_message(self) -> dict[str, Any]:
        chunks: list[bytes] = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x8:  # close
                raise ConnectionError("Home Assistant closed the WebSocket")
            chunks.append(payload)
            if fin:
                return json.loads(b"".join(chunks).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(0x1, json.dumps(payload).encode("utf-8"))

    def _authenticate(self, token: str) -> None:
        hello = self._recv_message()
        if hello.get("type") != "auth_required":
            raise ConnectionError(f"Unexpected WebSocket greeting: {hello.get('type')}")
        self._send_json({"type": "auth", "access_token": token})
        reply = self._recv_message()
        if reply.get("type") == "auth_invalid":
            raise PermissionError("Home Assistant rejected the access token")
        if reply.get("type") != "auth_ok":
            raise ConnectionError(f"Unexpected auth response: {reply.get('type')}")

    def call(self, payload: dict[str, Any]) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        self._send_json({"id": msg_id, **payload})
        while True:
            reply = self._recv_message()
            if reply.get("type") == "result" and reply.get("id") == msg_id:
                if reply.get("success"):
                    return reply.get("result")
                err = reply.get("error") or {}
                raise RuntimeError(err.get("message") or err.get("code") or "WebSocket call failed")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _fetch_diagnostics(base_url: str, token: str, entry_id: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/diagnostics/config_entry/{urllib.parse.quote(entry_id, safe='')}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def _collect_live(base_url: str, token: str) -> dict[str, Any]:
    ws = _HAWebSocket(base_url, token)

    def attempt(msg: dict[str, Any]) -> Any:
        try:
            return ws.call(msg)
        except Exception as err:
            return {"__error": str(err)}

    try:
        ha_config = attempt({"type": "get_config"})
        config_entries = attempt({"type": "config_entries/get"})
        states = attempt({"type": "get_states"})
        entity_registry = attempt({"type": "config/entity_registry/list"})
        device_registry = attempt({"type": "config/device_registry/list"})
        repairs = attempt({"type": "repairs/list_issues"})
        system_log = attempt({"type": "system_log/list"})

        manifests: dict[str, Any] = {}
        for domain in (DOMAIN, ORIGINAL_DOMAIN):
            manifest = attempt({"type": "manifest/get", "integration": domain})
            if isinstance(manifest, dict) and "__error" not in manifest:
                manifests[domain] = {
                    "version": manifest.get("version"),
                    "is_built_in": manifest.get("is_built_in"),
                }
    finally:
        ws.close()

    diagnostics: dict[str, Any] = {}
    if isinstance(config_entries, list):
        for entry in config_entries:
            if isinstance(entry, dict) and entry.get("domain") in DOMAINS and entry.get("entry_id"):
                eid = entry["entry_id"]
                try:
                    diagnostics[eid] = _fetch_diagnostics(base_url, token, eid)
                except (urllib.error.URLError, OSError, ValueError) as err:
                    diagnostics[eid] = {"__error": str(err)}

    return {
        "raw_schema": 1,
        "ha_config": ha_config,
        "config_entries": config_entries,
        "states": states,
        "entity_registry": entity_registry,
        "device_registry": device_registry,
        "repairs": repairs.get("issues", []) if isinstance(repairs, dict) and "issues" in repairs else repairs,
        "system_log": system_log,
        "manifests": manifests,
        "diagnostics": diagnostics,
    }


def _cmd_collect(args: argparse.Namespace) -> None:
    if args.raw:
        raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    else:
        base_url = args.url or os.environ.get("HA_URL")
        token = os.environ.get("HA_TOKEN")
        if not base_url or not token:
            _fail("set HA_URL and HA_TOKEN, or pass --raw <browser snapshot>.")
        try:
            raw = _collect_live(base_url, token)
        except Exception as err:
            _fail(str(err))

    if args.diagnostics:
        diag_payload = json.loads(Path(args.diagnostics).read_text(encoding="utf-8"))
        raw["diagnostics"] = diag_payload.get("data", diag_payload) if isinstance(diag_payload, dict) else {}

    report = build_report(
        raw,
        {
            "entry": args.entry,
            "integration_version": args.integration_version,
            "installation_type": args.installation_type,
            "agent_name": args.agent,
            "agent_model": args.model,
            "model": args.vacuum_model,
            "map_format": args.map_format,
        },
    )

    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    entries_found = report["integration"]["entries_found"]
    print(
        f"collected: {entries_found} entry(ies), {len(report['states'])} entities, "
        f"{len(report['registry'])} registry entries, {len(report['repairs'])} repairs",
        file=sys.stderr,
    )
    if entries_found > 1:
        print("several entries found; pick one with --entry <index or entry_id>", file=sys.stderr)
    missing = [f for f in missing_fields(report) if not f.startswith("problem.") and f != "agent.name"]
    if missing:
        print(f"still missing (fill in by hand): {', '.join(missing)}", file=sys.stderr)


def _cmd_render(args: argparse.Namespace) -> None:
    report = json.loads(Path(args.file).read_text(encoding="utf-8"))
    missing = missing_fields(report)
    if missing:
        _fail(f"fill these fields before filing: {', '.join(missing)}")
    sys.stdout.write(render_issue_body(report))
    print(f"title: {issue_title(report)}", file=sys.stderr)


def _cmd_check(args: argparse.Namespace) -> None:
    result = review_issue(Path(args.file).read_text(encoding="utf-8"))
    display = {
        **result,
        "redactedBody": "(secrets found, would be rewritten)" if result["redactedBody"] else None,
    }
    print(json.dumps(display, indent=2, ensure_ascii=False))
    if not result["complete"] or result["redactedBody"]:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Better Xiaomi Cloud Map Extractor agent report CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="Collect and redact integration state from Home Assistant")
    p_collect.add_argument("--url", help="Home Assistant URL (defaults to HA_URL)")
    p_collect.add_argument("--raw", help="Path to a browser snapshot JSON file")
    p_collect.add_argument("--diagnostics", help="Optional downloaded Home Assistant diagnostics JSON file")
    p_collect.add_argument("--out", help="Write report JSON to this path instead of stdout")
    p_collect.add_argument("--entry", help="Config entry index or entry_id when several exist")
    p_collect.add_argument("--integration-version", help="Override integration version if unavailable")
    p_collect.add_argument("--installation-type", help="Home Assistant installation type")
    p_collect.add_argument("--vacuum-model", help="Override vacuum model if unavailable")
    p_collect.add_argument("--map-format", help="Override map format if unavailable")
    p_collect.add_argument("--agent", help="Agent name (e.g. Claude Code, Codex, Cursor)")
    p_collect.add_argument("--model", help="Agent model identifier")
    p_collect.set_defaults(func=_cmd_collect)

    p_render = sub.add_parser("render", help="Render a completed report JSON into an issue body")
    p_render.add_argument("file", help="Path to report.json")
    p_render.set_defaults(func=_cmd_render)

    p_check = sub.add_parser("check", help="Validate an agent report Markdown file")
    p_check.add_argument("file", help="Path to issue.md")
    p_check.set_defaults(func=_cmd_check)

    parsed = parser.parse_args(argv)
    parsed.func(parsed)


if __name__ == "__main__":
    main()
