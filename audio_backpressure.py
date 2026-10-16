"""Bounded in-memory backlog between the microphone callback and the decoder.

A slow multi-model recheck inside one finalize blocks the single consumer
thread. With the old 80-packet (6.4 s) queue that block fired audio_overflow
and the app stopped a recording the user never asked to stop. This queue
holds a bounded ~120 s of packets instead: enough to ride out a slow recheck
without unbounded memory, and still a hard bound — a genuinely stuck decoder
fills it and the caller must stop with a truthful reason, never drop packets
silently and report normal operation.
"""
import queue
import threading
from collections import deque

CAPACITY_SECONDS = 120.0


def _packet_seconds(packet):
    samples = getattr(packet, 'samples', packet)
    rate = getattr(packet, 'sample_rate', None) or 16000
    return len(samples) / float(rate)


class BackpressureQueue:
    def __init__(self, capacity_seconds=CAPACITY_SECONDS):
        self.capacity_seconds = float(capacity_seconds)
        self._items = deque()
        self._seconds = 0.0
        self._lock = threading.Lock()

    def put_nowait(self, packet):
        seconds = _packet_seconds(packet)
        with self._lock:
            if self._seconds + seconds > self.capacity_seconds + 1e-9:
                raise queue.Full
            self._items.append(packet)
            self._seconds += seconds

    def get_nowait(self):
        with self._lock:
            if not self._items:
                raise queue.Empty
            packet = self._items.popleft()
            self._seconds = max(0.0, self._seconds - _packet_seconds(packet))
            return packet

    def empty(self):
        with self._lock:
            return not self._items

    def qsize(self):
        with self._lock:
            return len(self._items)

    def queued_seconds(self):
        """Backlog in seconds of audio, computed from each packet's real rate."""
        with self._lock:
            return self._seconds

    def clear(self):
        with self._lock:
            self._items.clear()
            self._seconds = 0.0
