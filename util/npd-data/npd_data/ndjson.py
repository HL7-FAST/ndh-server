"""Read NDJSON files, plain or zstd-compressed, one line at a time.

Files are read line by line; a full file is never held in memory.
"""

import io
from pathlib import Path

import orjson
import zstandard


def iter_ndjson(path, raw=False):
    """Yield one record per non-blank line of a .ndjson or .ndjson.zst file.

    With raw=True, yields the undecoded line text instead of a parsed dict,
    for callers that prefilter lines before paying JSON parse cost.
    """
    path = Path(path)
    if path.name.endswith(".zst"):
        with open(path, "rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            yield from _iter_lines(io.TextIOWrapper(reader, encoding="utf-8"), raw)
    else:
        with open(path, encoding="utf-8") as fh:
            yield from _iter_lines(fh, raw)


def _iter_lines(text_stream, raw):
    for line in text_stream:
        line = line.strip()
        if not line:
            continue
        yield line if raw else orjson.loads(line)
