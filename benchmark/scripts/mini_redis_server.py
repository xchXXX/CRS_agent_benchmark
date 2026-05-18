from __future__ import annotations

import argparse
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ValueEntry:
    value: bytes
    expires_at: float | None = None


class InMemoryRedis:
    def __init__(self) -> None:
        self._store: dict[bytes, ValueEntry] = {}
        self._lock = threading.RLock()

    def _purge_if_expired(self, key: bytes) -> None:
        entry = self._store.get(key)
        if entry is None:
            return
        if entry.expires_at is not None and entry.expires_at <= time.time():
            self._store.pop(key, None)

    def get(self, key: bytes) -> bytes | None:
        with self._lock:
            self._purge_if_expired(key)
            entry = self._store.get(key)
            return None if entry is None else entry.value

    def set(self, key: bytes, value: bytes, ex_seconds: float | None = None, px_millis: int | None = None) -> None:
        expires_at = None
        if px_millis is not None:
            expires_at = time.time() + (px_millis / 1000.0)
        elif ex_seconds is not None:
            expires_at = time.time() + ex_seconds
        with self._lock:
            self._store[key] = ValueEntry(value=value, expires_at=expires_at)

    def delete(self, *keys: bytes) -> int:
        deleted = 0
        with self._lock:
            for key in keys:
                self._purge_if_expired(key)
                if key in self._store:
                    self._store.pop(key, None)
                    deleted += 1
        return deleted

    def exists(self, key: bytes) -> int:
        with self._lock:
            self._purge_if_expired(key)
            return 1 if key in self._store else 0


STORE = InMemoryRedis()


class RedisProtocolError(Exception):
    pass


def read_line(rfile) -> bytes:
    line = rfile.readline()
    if not line:
        raise EOFError
    if not line.endswith(b"\r\n"):
        raise RedisProtocolError("invalid RESP line ending")
    return line[:-2]


def read_bulk_string(rfile) -> bytes | None:
    header = read_line(rfile)
    if not header.startswith(b"$"):
        raise RedisProtocolError("expected bulk string")
    size = int(header[1:])
    if size == -1:
        return None
    payload = rfile.read(size + 2)
    if len(payload) != size + 2 or not payload.endswith(b"\r\n"):
        raise RedisProtocolError("invalid bulk string payload")
    return payload[:-2]


def read_resp_array(rfile) -> list[bytes | None]:
    header = read_line(rfile)
    if not header:
        raise EOFError
    prefix = header[:1]
    if prefix == b"*":
        count = int(header[1:])
        items: list[bytes | None] = []
        for _ in range(count):
            items.append(read_bulk_string(rfile))
        return items
    if prefix == b"$":
        size = int(header[1:])
        if size == -1:
            return [None]
        payload = rfile.read(size + 2)
        if len(payload) != size + 2 or not payload.endswith(b"\r\n"):
            raise RedisProtocolError("invalid inline bulk payload")
        return [payload[:-2]]
    raise RedisProtocolError("unsupported RESP frame")


def encode_simple(message: str) -> bytes:
    return f"+{message}\r\n".encode("utf-8")


def encode_error(message: str) -> bytes:
    return f"-ERR {message}\r\n".encode("utf-8")


def encode_integer(value: int) -> bytes:
    return f":{value}\r\n".encode("utf-8")


def encode_bulk(value: bytes | None) -> bytes:
    if value is None:
        return b"$-1\r\n"
    return b"$" + str(len(value)).encode("ascii") + b"\r\n" + value + b"\r\n"


def normalize_command(item: bytes | None) -> str:
    if item is None:
        return ""
    return item.decode("utf-8", errors="replace").strip().upper()


def parse_float_token(item: bytes | None) -> float | None:
    if item is None:
        return None
    return float(item.decode("utf-8", errors="replace"))


def parse_int_token(item: bytes | None) -> int | None:
    if item is None:
        return None
    return int(item.decode("utf-8", errors="replace"))


def execute_command(parts: list[bytes | None]) -> bytes:
    if not parts:
        return encode_error("empty command")

    command = normalize_command(parts[0])

    if command in {"PING", "ECHO"}:
        if command == "PING" and len(parts) == 1:
            return encode_simple("PONG")
        payload = parts[1] if len(parts) > 1 else b"PONG"
        return encode_bulk(payload)

    if command == "CLIENT":
        return encode_simple("OK")

    if command == "HELLO":
        return encode_simple("OK")

    if command == "SELECT":
        return encode_simple("OK")

    if command == "COMMAND":
        return b"*0\r\n"

    if command == "GET":
        if len(parts) != 2 or parts[1] is None:
            return encode_error("wrong number of arguments for GET")
        return encode_bulk(STORE.get(parts[1]))

    if command == "SET":
        if len(parts) < 3 or parts[1] is None or parts[2] is None:
            return encode_error("wrong number of arguments for SET")
        key = parts[1]
        value = parts[2]
        ex_seconds = None
        px_millis = None
        idx = 3
        while idx < len(parts):
            token = normalize_command(parts[idx])
            if token == "EX" and idx + 1 < len(parts):
                ex_seconds = parse_float_token(parts[idx + 1])
                idx += 2
                continue
            if token == "PX" and idx + 1 < len(parts):
                px_millis = parse_int_token(parts[idx + 1])
                idx += 2
                continue
            if token in {"NX", "XX", "KEEPTTL", "GET"}:
                idx += 1
                continue
            idx += 1
        STORE.set(key, value, ex_seconds=ex_seconds, px_millis=px_millis)
        return encode_simple("OK")

    if command == "DEL":
        keys = [item for item in parts[1:] if item is not None]
        return encode_integer(STORE.delete(*keys))

    if command == "EXISTS":
        if len(parts) != 2 or parts[1] is None:
            return encode_error("wrong number of arguments for EXISTS")
        return encode_integer(STORE.exists(parts[1]))

    return encode_error(f"unsupported command {command}")


class RedisHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        while True:
            try:
                parts = read_resp_array(self.rfile)
            except EOFError:
                return
            except RedisProtocolError as exc:
                self.wfile.write(encode_error(str(exc)))
                self.wfile.flush()
                return

            try:
                response = execute_command(parts)
            except Exception as exc:  # pragma: no cover - defensive
                response = encode_error(str(exc))
            self.wfile.write(response)
            self.wfile.flush()


class ThreadedRedisServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal Redis-compatible server for benchmark local bootstrap.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with ThreadedRedisServer((args.host, args.port), RedisHandler) as server:
        server.serve_forever(poll_interval=0.25)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
