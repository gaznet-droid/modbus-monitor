#!/usr/bin/env python3
"""Epever dongle tap: a listen-only logger and a pass-through relay (test tool).

Standard library only. It never originates traffic towards the charger:

  listen  accept connections from the dongle and log what arrives; sends nothing.
  relay   accept connections from the dongle, pass every byte both ways to the
          upstream server unchanged, and log a copy.

Each log line is: ISO time, direction (dongle> or server>), byte count, hex.
Any Modbus RTU read replies found in the bytes (valid CRC) are noted after it.
The log goes in the Pi's private folder; it can hold the dongle's serial number.

    python3 epever_tap.py listen --port 15001 --log /var/lib/modpi/private/epever/tap.log
    python3 epever_tap.py relay --port 15000 --upstream HOST:15000 --log ...
"""
import argparse
import os
import selectors
import socket
import struct
import sys
import time


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def find_read_replies(data, unit=1):
    """Modbus RTU replies to read functions 03/04 anywhere in data: [(function, words)]."""
    found = []
    i = 0
    while i + 5 <= len(data):
        count = data[i + 2]
        end = i + 3 + count + 2
        if (data[i] == unit and data[i + 1] in (3, 4) and count and count % 2 == 0
                and end <= len(data) and crc16(data[i:end - 2]) == data[end - 2:end]):
            found.append((data[i + 1], list(struct.unpack(">%dH" % (count // 2), data[i + 3:end - 2]))))
            i = end
        else:
            i += 1
    return found


class Log:
    def __init__(self, path):
        self.out = sys.stdout
        if path:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self.out = os.fdopen(fd, "a", buffering=1)

    def note(self, text):
        self.out.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), text))
        self.out.flush()

    def data(self, direction, data):
        line = "%s %d %s" % (direction, len(data), data.hex())
        for function, words in find_read_replies(data):
            line += "  [modbus fc%02d %s]" % (function, " ".join("%04x" % w for w in words))
        self.note(line)


def serve(args):
    log = Log(args.log)
    upstream = None
    if args.mode == "relay":
        host, _, port = args.upstream.rpartition(":")
        upstream = (host, int(port))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.port))
    server.listen(4)
    sel = selectors.DefaultSelector()
    sel.register(server, selectors.EVENT_READ, None)
    peers = {}  # socket -> (direction label, the socket to copy to, or None)
    log.note("%s on port %d" % (args.mode, args.port))

    def close(sock):
        _, other = peers.pop(sock, (None, None))
        for s in (sock, other):
            if s is not None:
                peers.pop(s, None)
                try:
                    sel.unregister(s)
                except (KeyError, ValueError):
                    pass
                s.close()
        log.note("connection closed")

    while True:
        for key, _ in sel.select(timeout=60):
            sock = key.fileobj
            if sock is server:
                conn, _ = server.accept()
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                far = None
                if upstream:
                    try:
                        far = socket.create_connection(upstream, 15)
                    except OSError as exc:
                        log.note("upstream failed: %s" % exc.__class__.__name__)
                        conn.close()
                        continue
                    peers[far] = ("server>", conn)
                    sel.register(far, selectors.EVENT_READ, None)
                peers[conn] = ("dongle>", far)
                sel.register(conn, selectors.EVENT_READ, None)
                log.note("dongle connected")
                continue
            if sock not in peers:
                continue
            direction, other = peers[sock]
            try:
                data = sock.recv(4096)
                if data and other is not None:
                    other.sendall(data)
            except OSError:
                data = b""
            if not data:
                close(sock)
                continue
            log.data(direction, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=("listen", "relay"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--upstream", help="relay only: HOST:PORT of the real server")
    parser.add_argument("--log", help="append to this file (created 0600); default is the screen")
    args = parser.parse_args()
    if args.mode == "relay" and not args.upstream:
        parser.error("relay needs --upstream HOST:PORT")
    serve(args)


if __name__ == "__main__":
    main()
