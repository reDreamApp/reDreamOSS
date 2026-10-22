"""Give a variable-bitrate MP3 the Xing header it is missing.

Voxtral returns VBR MP3 without a Xing/VBRI header. A player then guesses the
length from the first frame's bitrate and seeks by the same guess: an 8.0 s
rehearsal showed as 5 s on Android (ExoPlayer) and 6.2 s in Core Audio. The
frame added here carries the real frame count, byte count and a seek table,
which is what players read instead of guessing.
"""

from __future__ import annotations

from typing import NamedTuple

_LAYER_III = 0b01
_MPEG1 = 0b11
_MONO = 0b11

# kbit/s by bitrate index, for Layer III.
_BITRATES_MPEG1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_MPEG2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
# Hz by sample-rate index, keyed by the header's version bits (0b01 is reserved).
_SAMPLE_RATES = {
    0b11: (44100, 48000, 32000),
    0b10: (22050, 24000, 16000),
    0b00: (11025, 12000, 8000),
}

_XING_FLAGS = 0x07  # frame count + byte count + seek table
_XING_BODY = 4 + 4 + 4 + 4 + 100


class _Frame(NamedTuple):
    offset: int
    size: int
    header: int


def add_seek_header(data: bytes) -> bytes:
    """Return `data` with a Xing header frame in front of its audio frames.

    Returns `data` unchanged when it is not Layer III audio or already carries
    a Xing, Info or VBRI header.
    """
    frames = _frames(data)
    if not frames:
        return data
    first = frames[0]
    first_frame = data[first.offset : first.offset + first.size]
    if any(tag in first_frame for tag in (b"Xing", b"Info", b"VBRI")):
        return data

    version = (first.header >> 19) & 0b11
    mono = (first.header >> 6) & 0b11 == _MONO
    if version == _MPEG1:
        side_info = 17 if mono else 32
    else:
        side_info = 9 if mono else 17

    # Same stream parameters as the audio, no CRC, no padding; the smallest
    # bitrate whose frame holds the tag. All-zero side info decodes as silence.
    needed = 4 + side_info + _XING_BODY
    for index in range(1, 15):
        header = (first.header & 0xFFFE0DFF) | 0x00010000 | (index << 12)
        size = _frame_size(header)
        if size >= needed:
            break
    else:
        return data

    audio_start = first.offset
    audio_end = frames[-1].offset + frames[-1].size
    total = size + (audio_end - audio_start)
    toc = bytes(
        min(255, 256 * (size + frames[i * len(frames) // 100].offset - audio_start) // total)
        for i in range(100)
    )
    tag = (
        b"Xing"
        + _XING_FLAGS.to_bytes(4, "big")
        + len(frames).to_bytes(4, "big")
        + total.to_bytes(4, "big")
        + toc
    )
    xing = bytearray(size)
    xing[0:4] = header.to_bytes(4, "big")
    xing[4 + side_info : 4 + side_info + len(tag)] = tag
    return data[:audio_start] + bytes(xing) + data[audio_start:]


def _frames(data: bytes) -> list[_Frame]:
    """The run of consecutive Layer III frames after any ID3v2 tag."""
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = data[6] << 21 | data[7] << 14 | data[8] << 7 | data[9]
        pos = 10 + size

    # A sync pattern can occur by chance; a real frame is followed by another.
    while pos + 4 <= len(data):
        size = _frame_size(int.from_bytes(data[pos : pos + 4], "big"))
        if size and _frame_size(int.from_bytes(data[pos + size : pos + size + 4], "big")):
            break
        pos += 1

    frames: list[_Frame] = []
    while pos + 4 <= len(data):
        header = int.from_bytes(data[pos : pos + 4], "big")
        size = _frame_size(header)
        if not size or pos + size > len(data):
            break
        frames.append(_Frame(pos, size, header))
        pos += size
    return frames


def _frame_size(header: int) -> int:
    """Size in bytes of the Layer III frame with this header, 0 if invalid."""
    if header >> 21 != 0x7FF or (header >> 17) & 0b11 != _LAYER_III:
        return 0
    version = (header >> 19) & 0b11
    bitrate_index = (header >> 12) & 0b1111
    rate_index = (header >> 10) & 0b11
    if version not in _SAMPLE_RATES or bitrate_index in (0, 15) or rate_index == 3:
        return 0
    sample_rate = _SAMPLE_RATES[version][rate_index]
    padding = (header >> 9) & 1
    if version == _MPEG1:
        return 144_000 * _BITRATES_MPEG1[bitrate_index] // sample_rate + padding
    return 72_000 * _BITRATES_MPEG2[bitrate_index] // sample_rate + padding
