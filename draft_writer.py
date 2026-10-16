"""One background writer coalesces snapshots; closing flushes the latest one."""
from datetime import datetime
import threading
import time
from draft_store import validate_draft
from draft_documents import write_current


class DraftWriter:
    def __init__(self, store, folder):
        self.store, self.folder = store, folder
        self.condition = threading.Condition()
        self.pending = None
        self.requested = self.saved = 0
        self.error = None
        self.document_error = None
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='draft-save', daemon=True)
        self.thread.start()

    def submit(self, data, profile):
        draft = validate_draft(data)
        with self.condition:
            if self.closed:
                raise RuntimeError('草稿保存已关闭')
            self.requested += 1
            self.pending = (self.requested, draft, str(profile))
            self.error = None
            self.condition.notify_all()
            return self.requested

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.pending is None and self.closed:
                    return
                serial, draft, profile = self.pending
                self.pending = None
            try:
                self.store.save(draft)
                try:
                    write_current(self.folder, profile, draft, datetime.now().astimezone().isoformat(timespec='seconds'))
                    self.document_error = None
                except (OSError, ValueError) as exc:
                    self.document_error = type(exc).__name__
                with self.condition:
                    self.saved = serial
                    self.error = None
                    self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.error = type(exc).__name__
                    self.condition.notify_all()

    def flush(self, timeout=5):
        end = time.monotonic() + timeout
        with self.condition:
            target = self.requested
            while self.saved < target:
                if self.error or not self.thread.is_alive():
                    return False
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def close(self, timeout=5):
        if not self.flush(timeout):
            return False
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout)
        return not self.thread.is_alive()
