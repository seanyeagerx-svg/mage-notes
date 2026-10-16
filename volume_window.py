"""User-controlled raw microphone RMS range, before gain and ASR.

Excluded packets become silence with the same duration. They cannot reach the
original-audio refiner or learning, and the endpoint clock remains intact.
"""
import math
import numpy as np


def limits(enabled=False, minimum=-65, maximum=-4):
    minimum, maximum = float(minimum), float(maximum)
    if not all(math.isfinite(v) for v in (minimum, maximum)) or not -80 <= minimum < maximum <= 0:
        raise ValueError('收音下限必须低于上限，范围为 -80 到 0 dB')
    return bool(enabled), minimum, maximum


def level_db(samples):
    if not len(samples):
        return -80.0
    rms = float(np.sqrt(np.mean(np.asarray(samples, dtype=np.float32) ** 2)))
    return max(-80.0, 20 * math.log10(max(rms, 1e-8))) if math.isfinite(rms) else 0.0


def filter_packet(samples, config):
    # Invalid sensor samples are not loud valid speech. In particular, mapping
    # NaN/Inf to 0 dB must not admit them at a user-selected upper limit of zero.
    if not np.all(np.isfinite(samples)):
        return np.zeros_like(samples), -80.0, False
    db = level_db(samples)
    enabled, low, high = config
    accepted = not enabled or low <= db <= high
    return (samples if accepted else np.zeros_like(samples)), db, accepted
