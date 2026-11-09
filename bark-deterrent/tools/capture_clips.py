#!/usr/bin/env python3
"""Save audio clips streamed by the bark-deterrent firmware as WAV files.

Two modes:

  Sound-triggered clips (for the "bark" class and other loud sounds):
      python tools/capture_clips.py /dev/ttyUSB0
  Every loud sound becomes a 1 s WAV in dataset/unlabeled/. Listen to them and
  sort them into folders (bark/, noise/, ...) before uploading to Edge Impulse.

  Continuous recording (for the "noise" / background class):
      python tools/capture_clips.py /dev/ttyUSB0 --rec 300
  Records 300 s of whatever the mic hears, as 1 s WAV files.

Other serial output (EVT, STAT, LEVEL lines...) is printed as it arrives.
Use --log FILE to also save those lines.
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import sys
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

DEFAULT_BAUD = 921600


@dataclass
class Clip:
    t_ms: int
    sample_rate: int
    reason: str
    pcm: bytes  # 16-bit little-endian mono


Message = Clip | str


class ProtocolError(Exception):
    pass


def read_messages(stream: BinaryIO) -> Iterator[Message]:
    """Yields text lines (str) and Clips from the firmware's serial stream.

    A clip is a text header "CLIP,<t_ms>,<samples>,<rate>,<reason>" followed by
    exactly samples*2 bytes of PCM and a newline.
    """
    while True:
        line = stream.readline()
        if not line:
            return
        if line.startswith(b"CLIP,"):
            parts = line.decode("ascii", errors="replace").strip().split(",")
            if len(parts) != 5:
                raise ProtocolError(f"bad clip header: {line!r}")
            _, t_ms, samples, rate, reason = parts
            n_bytes = int(samples) * 2
            pcm = _read_exact(stream, n_bytes)
            if len(pcm) != n_bytes:
                raise ProtocolError(f"clip truncated: got {len(pcm)} of {n_bytes} bytes")
            yield Clip(int(t_ms), int(rate), reason, pcm)
        else:
            yield line.decode("utf-8", errors="replace").rstrip("\r\n")


def _read_exact(stream: BinaryIO, n: int, timeout_s: float = 5.0) -> bytes:
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    while len(buf) < n and time.monotonic() < deadline:
        chunk = stream.read(n - len(buf))
        if chunk:
            buf.extend(chunk)
        elif not hasattr(stream, "in_waiting"):
            break  # plain file/BytesIO: no more data is coming
    return bytes(buf)


def write_wav(path: pathlib.Path, clip: Clip) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(clip.sample_rate)
        w.writeframes(clip.pcm)


def open_port(port: str, baud: int):
    import serial  # pyserial; imported here so the parser can be tested without it

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 1
    # Keep DTR/RTS low so opening the port doesn't reset the board or put it
    # into download mode.
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="serial port, e.g. /dev/ttyUSB0, /dev/cu.usbserial-*, COM5")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--out", default="dataset/unlabeled", help="folder for WAV files")
    ap.add_argument("--rec", type=int, metavar="SECONDS", help="record continuous audio instead of loud sounds")
    ap.add_argument("--log", help="also append text lines to this file")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    session = time.strftime("%Y%m%d-%H%M%S")

    with contextlib.ExitStack() as stack:
        log = stack.enter_context(open(args.log, "a", encoding="utf-8")) if args.log else None
        ser = open_port(args.port, args.baud)
        stack.callback(ser.close)
        time.sleep(0.5)
        ser.reset_input_buffer()
        command = f"rec {args.rec}\n" if args.rec else "capture on\n"
        ser.write(command.encode())
        print(f"sent '{command.strip()}', saving clips to {out}/ (Ctrl+C to stop)")

        saved = 0
        try:
            for msg in read_messages(ser):
                if isinstance(msg, Clip):
                    path = out / f"{session}_{saved:05d}_{msg.reason}.wav"
                    write_wav(path, msg)
                    saved += 1
                    print(f"saved {path}")
                    if args.rec and saved >= args.rec:
                        break
                elif msg:  # (the newline after each clip arrives as an empty line)
                    print(msg)
                    if log:
                        log.write(msg + "\n")
                        log.flush()
        except KeyboardInterrupt:
            pass
        finally:
            if not args.rec:
                ser.write(b"capture off\n")
    print(f"{saved} clip(s) saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
