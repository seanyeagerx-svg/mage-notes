"""Bounded binary messages over private inherited pipes; no audio files or ports."""
import json
import struct
import numpy as np

MAX_FRAMES = 16000 * 15
MAX_FLOATS = MAX_FRAMES * 2


def read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        part = stream.read(count - len(data))
        if not part:
            raise EOFError('分离引擎连接已关闭')
        data.extend(part)
    return bytes(data)


def read_message(stream):
    size = struct.unpack('<I', read_exact(stream, 4))[0]
    if not 2 <= size <= 4096:
        raise ValueError('分离引擎头部长度无效')
    header = json.loads(read_exact(stream, size).decode('utf-8'))
    if not isinstance(header, dict) or header.get('version') != 1:
        raise ValueError('分离引擎协议无效')
    count = header.get('floats', 0)
    if type(count) is not int or not 0 <= count <= MAX_FLOATS:
        raise ValueError('分离引擎音频长度无效')
    data = np.frombuffer(read_exact(stream, count * 4), dtype='<f4').copy()
    if not np.isfinite(data).all():
        raise ValueError('分离引擎音频值无效')
    return header, data


def write_message(stream, header, data=()):
    audio = np.asarray(data, dtype='<f4').reshape(-1)
    if len(audio) > MAX_FLOATS or not np.isfinite(audio).all():
        raise ValueError('分离引擎音频值无效')
    meta = dict(header, version=1, floats=len(audio))
    encoded = json.dumps(meta, ensure_ascii=True, allow_nan=False).encode('utf-8')
    if len(encoded) > 4096:
        raise ValueError('分离引擎头部长度无效')
    for payload in (struct.pack('<I', len(encoded)) + encoded, audio.tobytes()):
        view = memoryview(payload)
        while view:
            written = stream.write(view)
            if not written:
                raise EOFError('分离引擎连接已关闭')
            view = view[written:]
    stream.flush()
