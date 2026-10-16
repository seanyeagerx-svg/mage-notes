"""Optional, owned GPU helper with bounded startup, calls and shutdown."""
from pathlib import Path
import queue
import subprocess
import threading
import numpy as np
from owned_process import ChildLifetime
from separation_protocol import MAX_FRAMES, read_message, write_message

ROOT = Path(__file__).resolve().parent


class SeparationClient:
    def __init__(self, python=None, script=None, startup_timeout=40, call_timeout=3):
        self.python = Path(python or ROOT/'.venv-separation-gpu12/Scripts/python.exe')
        self.script = Path(script or ROOT/'separation_worker.py')
        self.startup_timeout, self.call_timeout = startup_timeout, call_timeout
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.lifecycle = threading.RLock()
        self.call_lock = threading.Lock()
        self.process = None
        self.owner = None
        self.error = None
        self.serial = 0
        self.thread = None

    def _bounded(self, operation, timeout):
        result = queue.Queue(maxsize=1)
        def run():
            try: result.put((True, operation()))
            except BaseException as exc: result.put((False, exc))
        thread = threading.Thread(target=run, name='speaker-pipe', daemon=True)
        thread.start()
        try:
            okay, value = result.get(timeout=timeout)
        except queue.Empty:
            self.error = 'TimeoutError'
            self.close()
            thread.join(1)
            raise TimeoutError('分离引擎超时，已保留原音')
        if not okay:
            raise value
        return value

    def warmup(self):
        with self.lifecycle:
            if self.closed.is_set() or self.thread is not None:
                return
            self.thread = threading.Thread(target=self._start, name='speaker-start', daemon=True)
            self.thread.start()

    def _start(self):
        try:
            with self.lifecycle:
                if self.closed.is_set(): return
                if not self.python.is_file() or not self.script.is_file():
                    raise FileNotFoundError('本地声音分离运行时未安装')
                self.owner = ChildLifetime()
                self.process = subprocess.Popen([str(self.python), '-X', 'utf8', str(self.script)],
                    cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, bufsize=0)
                try: self.owner.attach(self.process)
                except BaseException:
                    self.process.kill()
                    raise
                process = self.process
            header, audio = self._bounded(lambda: read_message(process.stdout), self.startup_timeout)
            if header.get('kind') != 'ready' or len(audio):
                raise ValueError('分离引擎未正确启动')
            with self.lifecycle:
                if not self.closed.is_set(): self.ready.set()
        except BaseException as exc:
            self.error = type(exc).__name__
            self.close()

    def separate(self, audio, rate=16000):
        values = np.asarray(audio, dtype=np.float32)
        if (not self.ready.is_set() or self.closed.is_set() or rate != 16000
                or values.ndim != 1 or not 16000 <= len(values) <= MAX_FRAMES
                or not np.isfinite(values).all()):
            return None
        with self.call_lock:
            with self.lifecycle:
                if self.closed.is_set() or not self.ready.is_set(): return None
                process = self.process
                self.serial += 1
                request_id = self.serial
            def exchange():
                write_message(process.stdin, dict(kind='separate',id=request_id,sample_rate=rate),values)
                return read_message(process.stdout)
            try:
                header, data = self._bounded(exchange, self.call_timeout)
                if header.get('id') != request_id:
                    raise ValueError('分离结果不属于当前录音')
                if header.get('kind') == 'error':
                    raise RuntimeError(str(header.get('error','unknown'))[:40])
                if (header.get('kind') != 'separated' or header.get('frames') != len(values)
                        or len(data) != len(values)*2):
                    raise ValueError('分离结果长度无效')
                return list(data.reshape(2,-1))
            except Exception as exc:
                self.error = type(exc).__name__
                self.close()
                return None

    def close(self):
        with self.lifecycle:
            self.closed.set()
            self.ready.clear()
            owner, self.owner = self.owner, None
            process = self.process
            if owner: owner.close()
            if process:
                if process.poll() is None:
                    try: process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream:
                        try: stream.close()
                        except OSError: pass

    def __del__(self):
        try: self.close()
        except Exception: pass
