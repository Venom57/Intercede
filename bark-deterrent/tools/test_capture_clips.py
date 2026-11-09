"""Tests for the serial clip parser. Run: python -m unittest discover tools"""

import io
import pathlib
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from capture_clips import Clip, ProtocolError, read_messages, write_wav


def clip_bytes(samples, t_ms=1234, rate=16000, reason="onset"):
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    return f"CLIP,{t_ms},{len(samples)},{rate},{reason}\r\n".encode() + pcm + b"\r\n"


class ReadMessagesTest(unittest.TestCase):
    def test_text_and_clips_interleaved(self):
        # Include bytes that look like newlines inside the PCM payload.
        samples = [0, 10, -10, 0x0A0A, -32768, 32767]
        stream = io.BytesIO(
            b"I booting\r\n"
            + clip_bytes(samples)
            + b"EVT,5000,bark,0.910,0\r\n"
            + clip_bytes([1, 2, 3], t_ms=9, reason="rec")
        )
        msgs = list(read_messages(stream))
        self.assertEqual(msgs[0], "I booting")
        self.assertIsInstance(msgs[1], Clip)
        self.assertEqual(struct.unpack("<6h", msgs[1].pcm), tuple(samples))
        self.assertEqual((msgs[1].t_ms, msgs[1].sample_rate, msgs[1].reason), (1234, 16000, "onset"))
        # The newline after a clip payload shows up as an empty text line.
        self.assertEqual(msgs[2], "")
        self.assertEqual(msgs[3], "EVT,5000,bark,0.910,0")
        self.assertEqual(msgs[4].reason, "rec")

    def test_truncated_clip_raises(self):
        stream = io.BytesIO(b"CLIP,1,100,16000,onset\n" + b"\x00" * 10)
        with self.assertRaises(ProtocolError):
            list(read_messages(stream))

    def test_write_wav(self):
        clip = Clip(0, 16000, "onset", struct.pack("<4h", 1, -1, 2, -2))
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "x.wav"
            write_wav(path, clip)
            with wave.open(str(path)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                                 (1, 2, 16000, 4))


if __name__ == "__main__":
    unittest.main()
