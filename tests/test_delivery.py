import http.server
import json
import threading

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc


class Handler(http.server.BaseHTTPRequestHandler):
    posts = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.posts.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


def test_send_report_delivers_callback(patch_llm):
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Handler.posts = []
    callback_url = f"http://127.0.0.1:{server.server_address[1]}/callback"
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("17 событий")],
        "Ты — репортёр": [
            ai(calls=[tc("send_report", {
                "title": "Расследование", "markdown": "# 17 событий", "callback_url": callback_url,
            })]),
            ai("Отчёт доставлен"),
        ],
    })
    patch_llm(agent, FakeStructured(intent()))

    try:
        result = invoke(build_graph(), "доставь отчёт", thread_id="delivery", callback_url=callback_url)
    finally:
        server.shutdown()
        thread.join()

    assert len(Handler.posts) == 1
    assert Handler.posts[0]["event"] == "investigation_report"
    assert Handler.posts[0]["thread_id"] == "delivery"
    assert len(result["artifacts"]) == 2
    assert result["messages"][-1].content == "Отчёт доставлен"
