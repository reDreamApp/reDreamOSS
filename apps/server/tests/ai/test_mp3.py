"""Xing header for the headerless VBR MP3 that Voxtral returns."""

from __future__ import annotations

from redream_ai.audio.mp3 import add_seek_header

_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x04abcd"  # 10-byte header + 4 bytes of tag
_MPEG2_KBPS = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)


def _frame(kbps: int, *, fill: int) -> bytes:
    """One MPEG-2 Layer III frame, 22.05 kHz mono, no CRC, no padding."""
    header = 0xFFF300C0 | (_MPEG2_KBPS.index(kbps) << 12)
    size = 72_000 * kbps // 22_050
    return header.to_bytes(4, "big") + bytes([fill]) * (size - 4)


def _vbr(count: int = 200) -> bytes:
    rates = (80, 24, 48, 64, 32)
    return b"".join(_frame(rates[i % len(rates)], fill=1 + i % 200) for i in range(count))


def test_adds_a_xing_frame_in_front_of_the_audio():
    audio = _vbr(200)
    out = add_seek_header(_ID3 + audio)

    assert out.startswith(_ID3)
    assert out.endswith(audio)
    xing_frame = out[len(_ID3) : len(out) - len(audio)]
    # Same stream parameters as the audio frames, at a bitrate that fits the tag.
    assert xing_frame[:2] == audio[:2]
    # MPEG-2 mono: the tag sits behind the header and 9 bytes of side info.
    tag = xing_frame[13:]
    assert tag[:4] == b"Xing"
    assert int.from_bytes(tag[4:8], "big") == 0x07
    assert int.from_bytes(tag[8:12], "big") == 200
    assert int.from_bytes(tag[12:16], "big") == len(xing_frame) + len(audio)


def test_seek_table_maps_time_to_byte_position():
    audio = _vbr(200)
    out = add_seek_header(audio)
    start = out.index(b"Xing") + 16
    toc = list(out[start : start + 100])

    assert toc == sorted(toc)
    assert toc[0] == 256 * (len(out) - len(audio)) // len(out)
    # Half the playing time is half the frames, here also about half the bytes.
    assert abs(toc[50] - 128) <= 2


def test_is_idempotent():
    once = add_seek_header(_ID3 + _vbr())
    assert add_seek_header(once) == once


def test_leaves_other_data_alone():
    for data in (b"", b"ID3-fake-mp3", b"RIFF....WAVEfmt ", b"\xff\xf3\x90\xc0lonely"):
        assert add_seek_header(data) == data
