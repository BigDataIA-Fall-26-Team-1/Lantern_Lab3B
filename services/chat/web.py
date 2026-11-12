"""Part 0: the web chat, on Python's built-in http.server (no extra packages).

    PYTHONPATH=src python -m services.chat.web            # http://127.0.0.1:8765

Listens on 127.0.0.1 only. The login form takes principal, session token and
mandate; open_session checks the token. On success the server keeps the
session in memory under a random id, sent to the browser as an HttpOnly
cookie. The session stays bound to the mandate chosen at login: the chat
route never reads a mandate, and switching clients means logging out.
"""
import argparse
import html
import secrets
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from services.chat.session import ROOT, load_config, open_session, report_lines

PLACEHOLDER = ("(Part 0: this session is bound to {m}. The agent loop that answers "
               "arrives in Part 2.)")

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Credit Watch chat</title><style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#1f2937}}
h1{{font-size:20px}} pre{{background:#f3f4f6;padding:10px;overflow-x:auto}}
.err{{color:#b91c1c}} .me{{color:#1f3b5c}} .bot{{color:#4b5563}}
input,select,button{{font-size:15px;padding:6px;margin:4px 0}}
</style></head><body><h1>Credit Watch chat</h1>{body}</body></html>"""


def _login_form(mandates, principal, error=""):
    opts = "".join(f'<option value="{html.escape(m)}">{html.escape(m)}</option>' for m in mandates)
    err = f'<p class="err">Login refused: {html.escape(error)}</p>' if error else ""
    return f"""{err}<form method="post" action="/login">
<label>Principal<br><input name="principal" value="{html.escape(principal)}"></label><br>
<label>Session token<br><input name="token" type="password" autocomplete="off"></label><br>
<label>Mandate (one per session)<br><select name="mandate">{opts}</select></label><br>
<button type="submit">Log in</button></form>"""


def _chat_page(entry):
    o, history = entry["open"], entry["history"]
    m = o.session.mandate
    lines = "\n".join(html.escape(x) for x in report_lines(o.report))
    msgs = "".join(f'<p class="{c}">{html.escape(t)}</p>' for c, t in history)
    return f"""<p>Logged in as <b>{html.escape(o.session.principal)}</b>. Session bound to
<b>{html.escape(m)}</b> ({html.escape(o.client)}).</p>
<p>Context assembled for this mandate only:</p><pre>{lines}</pre>{msgs}
<form method="post" action="/chat"><input name="message" size="60" autofocus>
<button type="submit">Send</button></form>
<form method="post" action="/logout"><button type="submit">Log out</button></form>
<p>To work on another client, log out and start a new session.</p>"""


def make_server(host="127.0.0.1", port=8765, root=ROOT):
    P, _, mandates = load_config(root)
    sessions = {}                                   # session id -> {"open", "history"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, headers=()):
            data = PAGE.format(body=body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, headers=()):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()

        def _form(self):
            n = int(self.headers.get("Content-Length") or 0)
            q = parse_qs(self.rfile.read(n).decode())
            return {k: v[0] for k, v in q.items()}

        def _sid(self):
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            sid = c["sid"].value if "sid" in c else None
            return sid if sid in sessions else None

        def do_GET(self):
            if self.path != "/":
                return self._send(404, "<p>Not found.</p>")
            sid = self._sid()
            if sid:
                return self._send(200, _chat_page(sessions[sid]))
            return self._send(200, _login_form(mandates, P["agent"]["principal"]))

        def do_POST(self):
            f = self._form()
            if self.path == "/login":
                try:
                    o = open_session(f.get("principal", ""), f.get("token", ""), f.get("mandate", ""), root)
                except PermissionError as e:
                    return self._send(401, _login_form(mandates, P["agent"]["principal"], str(e)))
                sid = secrets.token_urlsafe(32)
                sessions[sid] = {"open": o, "history": []}
                return self._redirect([("Set-Cookie", f"sid={sid}; HttpOnly; SameSite=Strict; Path=/")])
            sid = self._sid()
            if self.path == "/chat" and sid:
                e = sessions[sid]                     # the mandate comes from the session, never the form
                msg = f.get("message", "").strip()
                if msg:
                    e["history"] += [("me", msg), ("bot", PLACEHOLDER.format(m=e["open"].session.mandate))]
                return self._redirect()
            if self.path == "/logout":
                sessions.pop(sid, None)
                return self._redirect([("Set-Cookie", "sid=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/")])
            return self._redirect()                   # no valid session: back to the login form

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Credit Watch chat (web)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    srv = make_server(port=args.port)
    print(f"Credit Watch chat on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
