"""Bounded, non-content session stop diagnostics.

Records only when a session ended and why: timestamp, reason, audio-queue
backlog in seconds and the program version. Never transcript text, audio or
any other content. The reason stays visible in the UI until the next session
starts instead of being overwritten by a generic "已结束" message.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from runtime_status import VERSION

LOG_LIMIT = 200

REASONS = {
    'buffer_exhausted': '识别复核长时间阻塞，录音缓冲已耗尽',
    'device_error': '收音或识别出现异常',
    'input_error': '目标输入框写入失败，转写副本已保留',
    'hotkey_stop': '快捷键结束',
    'button_stop': '按钮结束',
    'voice_send': '语音发送后自动结束',
    'workflow_command': '工作流口令结束',
    'pause': '会话暂停',
    'quit': '退出程序',
    'session_end_auto': '会话自动结束',
}


class SessionLog:
    def __init__(self, folder, limit=LOG_LIMIT):
        self.path = Path(folder) / 'session-diagnostics.jsonl'
        self.limit = int(limit)
        self._lock = threading.Lock()

    def record(self, reason, queue_seconds=None):
        entry = dict(time=datetime.now().astimezone().isoformat(),
                     reason=reason,
                     queue_seconds=(round(float(queue_seconds), 3)
                                    if queue_seconds is not None else None),
                     version=VERSION)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                if self.path.exists():
                    lines = self.path.read_text('utf-8').splitlines()[-(self.limit - 1):]
                lines.append(json.dumps(entry, ensure_ascii=False))
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text('\n'.join(lines) + '\n', 'utf-8')
                os.replace(temporary, self.path)
            except OSError:
                pass
        return entry

    @staticmethod
    def describe(reason):
        return REASONS.get(reason, '')
