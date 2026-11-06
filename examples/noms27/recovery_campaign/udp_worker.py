"""Namespace-local independent UDP traffic worker (Python standard library only)."""

import argparse
import json
import signal
import socket
import struct
import time
from pathlib import Path

HEADER = struct.Struct("!QQ")
PAYLOAD = 1200
PORT = 54321


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("send", "receive"))
    parser.add_argument("--address", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start-ns", type=int, required=True)
    parser.add_argument("--end-ns", type=int, required=True)
    parser.add_argument("--rate", type=float, required=True)
    args = parser.parse_args()
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    filename = "udp-sends.jsonl" if args.role == "send" else "udp-arrivals.jsonl"
    with sock, (args.root / filename).open("x", buffering=1) as stream:

        def emit(row):
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")

        try:
            if args.role == "receive":
                sock.bind((args.address, PORT))
                sock.settimeout(0.05)
                (args.root / "udp-ready.json").write_text(
                    json.dumps(
                        {
                            "tick_ns": time.monotonic_ns(),
                            "clock": "CLOCK_MONOTONIC",
                            "payload_bytes": PAYLOAD,
                            "address": args.address,
                        }
                    )
                )
                received = 0
                while running and time.monotonic_ns() < args.end_ns:
                    try:
                        data, _ = sock.recvfrom(65535)
                    except TimeoutError:
                        continue
                    arrival = time.monotonic_ns()
                    if len(data) != PAYLOAD:
                        emit(
                            {
                                "kind": "error",
                                "reason": "bad_payload_length",
                                "length": len(data),
                            }
                        )
                        continue
                    seq, sent = HEADER.unpack_from(data)
                    received += 1
                    emit(
                        {
                            "kind": "arrival",
                            "seq": seq,
                            "send_ns": sent,
                            "arrival_ns": arrival,
                            "payload_bytes": len(data),
                        }
                    )
                emit({"kind": "receiver_summary", "received": received})
            else:
                seq = missed = 0
                period = 1e9 / args.rate
                while running:
                    scheduled = args.start_ns + round(seq * period)
                    if scheduled >= args.end_ns:
                        break
                    delay = (scheduled - time.monotonic_ns()) / 1e9
                    if delay > 0:
                        time.sleep(delay)
                    if not running:
                        break
                    sent = time.monotonic_ns()
                    skipped = int(max(0, sent - scheduled) / period)
                    if skipped:
                        missed += skipped
                        seq += skipped
                        scheduled = args.start_ns + round(seq * period)
                    sock.sendto(
                        HEADER.pack(seq, sent) + bytes(PAYLOAD - HEADER.size),
                        (args.address, PORT),
                    )
                    emit(
                        {
                            "kind": "send",
                            "seq": seq,
                            "send_ns": sent,
                            "scheduled_ns": scheduled,
                        }
                    )
                    seq += 1
                emit(
                    {
                        "kind": "sender_summary",
                        "missed_slots": missed,
                        "next_sequence": seq,
                    }
                )
        except Exception as error:
            emit({"kind": "error", "reason": str(error)})
            raise


if __name__ == "__main__":
    main()
