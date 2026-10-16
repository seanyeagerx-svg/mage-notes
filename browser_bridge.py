"""Loopback-only extension bridge. Single-use pairing; authenticated project queue."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time

PORT = 8876
CLIENT = re.compile(r'^[a-p]{32}$')
MAX_BODY = 32 * 1024 * 1024


def pairing_file(root):
    return Path(root) / 'data' / 'browser-bridge.json'


class BrowserBridge:
    def __init__(self, root, projects, port=PORT):
        self.root = Path(root)
        self.projects = projects
        self.auth_path = pairing_file(self.root)
        self.auth = {}
        try:
            auth = json.loads(self.auth_path.read_text('utf-8'))
            if CLIENT.fullmatch(auth.get('client', '')) and re.fullmatch(r'[0-9a-f]{64}', auth.get('token_hash', '')):
                self.auth = auth
        except (OSError, ValueError, TypeError):
            pass
        self.nonce = None
        self.nonce_until = 0
        self.last_seen = 0
        self.version = None
        self.lock = threading.Lock()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # No prompts, pairing secrets, or browser credentials in logs.

            def reply(self, status, data, mime='application/json'):
                raw = data.encode('utf-8') if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                origin = self.headers.get('Origin', '')
                if re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def valid_host(self):
                return self.headers.get('Host') == f'127.0.0.1:{bridge.port}'

            def authorized(self):
                auth = bridge.auth
                client = self.headers.get('X-Magic-Client', '')
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                origin = self.headers.get('Origin', '')
                return (self.valid_host() and client == auth.get('client')
                        and (not origin or origin == 'chrome-extension://' + client)
                        and bool(token) and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), auth.get('token_hash', '')))

            def do_OPTIONS(self):
                if not self.valid_host() or not re.fullmatch(r'chrome-extension://[a-p]{32}', self.headers.get('Origin', '')):
                    self.reply(403, {'error': '连接来源无效。'})
                    return
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-Magic-Client')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.end_headers()

            def do_GET(self):
                if not self.valid_host():
                    self.reply(403, {'error': '连接地址无效。'})
                    return
                if self.path == '/connect':
                    self.reply(200, '<!doctype html><html lang="zh"><meta charset="utf-8"><title>连接魔法师手札</title><body style="font:18px system-ui;margin:10vh auto;max-width:650px;padding:24px"><h1>连接魔法师手札</h1><p id="magic-status">正在等待创作接力站连接。连接后可返回手札说话。</p><p>若一直没有连接，请在浏览器扩展页重新加载新版创作接力站。</p></body></html>', 'text/html')
                    return
                if not self.authorized():
                    self.reply(401, {'error': '扩展尚未连接。'})
                    return
                bridge.last_seen = time.time()
                if self.path == '/v1/next':
                    self.reply(200, {'job': bridge.projects.claim()})
                else:
                    self.reply(404, {'error': '未知连接步骤。'})

            def do_POST(self):
                try:
                    if not self.valid_host():
                        raise ValueError('连接地址无效。')
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= MAX_BODY:
                        raise ValueError('连接内容过长或为空。')
                    if self.path != '/v1/pair' and not self.authorized():
                        self.reply(401, {'error': '扩展尚未连接。'})
                        return
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError('连接内容无效。')
                    if self.path == '/v1/pair':
                        self.reply(200, bridge.pair(data, self.headers.get('Origin', '')))
                        return
                    bridge.last_seen = time.time()
                    if self.path == '/v1/hello':
                        bridge.version = str(data.get('version', ''))[:30]
                    elif re.fullmatch(r'/v1/turn/native-[0-9a-f]{32}', self.path):
                        event=data.get('event', {})
                        if not isinstance(event,dict):raise ValueError('回执格式无效。')
                        bridge.projects.update(self.path.rsplit('/', 1)[-1], data.get('lease'), event)
                    else:
                        self.reply(404, {'error': '未知连接步骤。'})
                        return
                    self.reply(200, {'ok': True})
                except (ValueError, TypeError, KeyError):
                    self.reply(400, {'error': '回执无效或任务发生变化，原内容已保留。'})

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def connected(self):
        return time.time() - self.last_seen < 45

    def pair_url(self):
        with self.lock:
            self.nonce = secrets.token_urlsafe(32)
            self.nonce_until = time.time() + 180
            return f'http://127.0.0.1:{self.port}/connect#' + self.nonce

    def pair(self, data, origin=''):
        client = data.get('clientId', '')
        if not isinstance(client, str) or not CLIENT.fullmatch(client) or (origin and origin != 'chrome-extension://' + client):
            raise ValueError('扩展来源无效。')
        with self.lock:
            nonce = data.get('nonce', '')
            if (not self.nonce or time.time() >= self.nonce_until or not isinstance(nonce, str)
                    or not hmac.compare_digest(nonce, self.nonce)):
                raise ValueError('连接已失效。')
            token = secrets.token_urlsafe(40)
            self.auth = dict(client=client, token_hash=hashlib.sha256(token.encode()).hexdigest())
            self.auth_path.parent.mkdir(exist_ok=True)
            self.auth_path.write_text(json.dumps(self.auth), 'utf-8')
            self.nonce = None
            self.last_seen = time.time()
            return dict(token=token, baseUrl=f'http://127.0.0.1:{self.port}', version='1.0.0-beta7')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
