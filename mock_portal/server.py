"""HTTP mock portal: login + session cookie, CSRF protected two-step form.

Endpoints:
  GET  /login          login page
  POST /login          authenticate, sets the session cookie
  GET  /form/step1     order number + amount form (session required)
  POST /form/step1     validates CSRF token + fields, stores a draft
  GET  /form/step2     remark form (session + draft required)
  POST /form/step2     validates CSRF token + remark, saves the record
  GET  /result         shows the saved record

CSRF tokens are single-use. A POST with a missing/invalid/consumed token
gets a 403 page that embeds a fresh token so the client can retry.
"""

import html
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

COOKIE_NAME = "portal_session"


class PortalState:
    def __init__(self, config):
        self.users = dict(config.get("users", {}))
        self.amount_min = float(config.get("amount_min", 0.01))
        self.amount_max = float(config.get("amount_max", 10000))
        self.remark_max = int(config.get("remark_max", 50))
        self.delay = float(config.get("request_delay_ms", 0)) / 1000.0
        self.records = {}
        for rec in config.get("seed_records", []):
            self.records[str(rec["order_no"])] = dict(rec)
        self.sessions = {}
        self.lock = threading.Lock()


def _esc(value):
    return html.escape(str(value), quote=True)


def _page(title, body):
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title></head><body><h1>{_esc(title)}</h1>"
        f"{body}</body></html>"
    )


def _error_html(field, value, message):
    return (
        f"<p class='error' data-field='{_esc(field)}' "
        f"data-value='{_esc(value)}'>{_esc(message)}</p>"
    )


def login_page(error=None):
    err = _error_html("login", "", error) if error else ""
    return _page(
        "登录",
        err
        + "<form method='post' action='/login'>"
        + "<label>账号<input name='username'></label>"
        + "<label>密码<input name='password' type='password'></label>"
        + "<button type='submit'>登录</button></form>",
    )


def step1_page(state, token, error=None, field="", value="", old=None):
    old = old or {}
    err = _error_html(field, value, error) if error else ""
    return _page(
        "录入 第一步",
        err
        + "<form method='post' action='/form/step1'>"
        + f"<input type='hidden' name='csrf_token' value='{_esc(token)}'>"
        + f"<label>单号<input name='order_no' value='{_esc(old.get('order_no', ''))}'></label>"
        + f"<label>金额<input name='amount' value='{_esc(old.get('amount', ''))}' "
        + f"min='{_esc(state.amount_min)}' max='{_esc(state.amount_max)}'></label>"
        + "<button type='submit'>下一步</button></form>",
    )


def step2_page(state, token, error=None, field="", value="", old=None):
    old = old or {}
    err = _error_html(field, value, error) if error else ""
    return _page(
        "录入 第二步",
        err
        + "<form method='post' action='/form/step2'>"
        + f"<input type='hidden' name='csrf_token' value='{_esc(token)}'>"
        + f"<label>备注<textarea name='remark' maxlength='{state.remark_max}'>"
        + f"{_esc(old.get('remark', ''))}</textarea></label>"
        + "<button type='submit'>提交</button></form>",
    )


def result_page(record):
    return _page(
        "提交成功",
        "<dl>"
        f"<dt>单号</dt><dd>{_esc(record['order_no'])}</dd>"
        f"<dt>金额</dt><dd>{_esc(record['amount'])}</dd>"
        f"<dt>备注</dt><dd>{_esc(record['remark'])}</dd>"
        "</dl>",
    )


def csrf_error_page(state, path, token):
    body = _error_html("csrf_token", "", "CSRF token 无效或已过期")
    if path == "/form/step1":
        body += step1_page(state, token)
    else:
        body += step2_page(state, token)
    return body


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _delay(self):
            if state.delay:
                time.sleep(state.delay)

        def _session_id(self):
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                if "=" in part:
                    key, val = part.strip().split("=", 1)
                    if key == COOKIE_NAME and val in state.sessions:
                        return val
            return None

        def _send(self, status, body, headers=None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location, headers=None):
            self.send_response(303)
            self.send_header("Location", location)
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()

        def _read_form(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            return {k: v[0] for k, v in parse_qs(raw).items()}

        def _new_token(self, sid):
            token = secrets.token_hex(16)
            with state.lock:
                state.sessions[sid]["tokens"].add(token)
            return token

        def _consume_token(self, sid, token):
            with state.lock:
                tokens = state.sessions[sid]["tokens"]
                if token and token in tokens:
                    tokens.discard(token)
                    return True
            return False

        def _require_session(self):
            sid = self._session_id()
            if sid is None:
                self._send(403, _page("未登录", "<p>未登录或会话已过期</p>"))
                return None
            return sid

        def _check_csrf(self, sid, form, path):
            if self._consume_token(sid, form.get("csrf_token", "")):
                return True
            self._send(403, csrf_error_page(state, path, self._new_token(sid)))
            return False

        def do_GET(self):
            self._delay()
            path = urlparse(self.path).path
            if path in ("/", "/login"):
                self._send(200, login_page())
                return
            if path == "/form/step1":
                sid = self._require_session()
                if sid:
                    self._send(200, step1_page(state, self._new_token(sid)))
                return
            if path == "/form/step2":
                sid = self._require_session()
                if not sid:
                    return
                if not state.sessions[sid].get("draft"):
                    self._redirect("/form/step1")
                    return
                self._send(200, step2_page(state, self._new_token(sid)))
                return
            if path == "/result":
                sid = self._require_session()
                if not sid:
                    return
                query = parse_qs(urlparse(self.path).query)
                order_no = query.get("order_no", [""])[0]
                record = state.records.get(order_no)
                if record is None:
                    self._send(404, _page("未找到", "<p>记录不存在</p>"))
                    return
                self._send(200, result_page(record))
                return
            self._send(404, _page("未找到", "<p>页面不存在</p>"))

        def do_POST(self):
            self._delay()
            path = urlparse(self.path).path
            form = self._read_form()
            if path == "/login":
                self._do_login(form)
                return
            if path == "/form/step1":
                self._do_step1(form)
                return
            if path == "/form/step2":
                self._do_step2(form)
                return
            self._send(404, _page("未找到", "<p>页面不存在</p>"))

        def _do_login(self, form):
            username = form.get("username", "")
            password = form.get("password", "")
            if not username or state.users.get(username) != password:
                self._send(403, login_page("账号或密码错误"))
                return
            sid = secrets.token_hex(16)
            with state.lock:
                state.sessions[sid] = {"tokens": set(), "draft": None}
            cookie = f"{COOKIE_NAME}={sid}; Path=/; HttpOnly"
            self._redirect("/form/step1", {"Set-Cookie": cookie})

        def _do_step1(self, form):
            sid = self._require_session()
            if not sid:
                return
            if not self._check_csrf(sid, form, "/form/step1"):
                return
            order_no = form.get("order_no", "").strip()
            amount_raw = form.get("amount", "").strip()
            old = {"order_no": order_no, "amount": amount_raw}
            token = self._new_token(sid)
            if not order_no:
                self._send(200, step1_page(state, token, "单号不能为空", "order_no", order_no, old))
                return
            if order_no in state.records:
                self._send(
                    200,
                    step1_page(state, token, f"单号重复：{order_no} 已存在", "order_no", order_no, old),
                )
                return
            try:
                amount = float(amount_raw)
            except ValueError:
                amount = None
            if amount is None or not (state.amount_min <= amount <= state.amount_max):
                self._send(
                    200,
                    step1_page(
                        state,
                        token,
                        f"金额超出范围：{amount_raw} 不在 [{state.amount_min}, {state.amount_max}] 内",
                        "amount",
                        amount_raw,
                        old,
                    ),
                )
                return
            with state.lock:
                state.sessions[sid]["draft"] = {"order_no": order_no, "amount": amount}
            self._redirect("/form/step2")

        def _do_step2(self, form):
            sid = self._require_session()
            if not sid:
                return
            if not self._check_csrf(sid, form, "/form/step2"):
                return
            draft = state.sessions[sid].get("draft")
            if not draft:
                self._redirect("/form/step1")
                return
            remark = form.get("remark", "")
            token = self._new_token(sid)
            if len(remark) > state.remark_max:
                self._send(
                    200,
                    step2_page(
                        state,
                        token,
                        f"备注过长：当前 {len(remark)} 字，上限 {state.remark_max} 字",
                        "remark",
                        remark,
                        {"remark": remark},
                    ),
                )
                return
            record = {
                "order_no": draft["order_no"],
                "amount": draft["amount"],
                "remark": remark,
            }
            with state.lock:
                state.records[draft["order_no"]] = record
                state.sessions[sid]["draft"] = None
            self._redirect(f"/result?order_no={draft['order_no']}")

    return Handler


class PortalServer:
    """In-process portal bound to an ephemeral localhost port."""

    def __init__(self, config):
        self.state = PortalState(config)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

