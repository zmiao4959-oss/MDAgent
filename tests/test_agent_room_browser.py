"""Exercise mode selection and public transcript rendering in Chromium."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

playwright = pytest.importorskip("playwright.sync_api")
STATIC = Path(__file__).resolve().parents[1] / "miniclaw/channels/web/static"


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        try:
            instance = driver.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def room_url():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    start = index.index('  <div id="collaboration-dialog"')
    end = index.index('  <script type="module" src="/static/app.js', start)
    html = ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="/static/style.css">'
            '<link rel="stylesheet" href="/static/agent-graph.css">'
            '<link rel="stylesheet" href="/static/collaboration.css"></head><body>'
            '<button id="btn-collaboration">协作方式</button><button id="btn-agent-graph">画布</button>'
            + index[start:end] + """<script type="module">
            import {createCollaborationPanel} from '/static/collaboration.js';
            window.testRoom = {chat: 'webchat:one', project: 'project:one', running: false, applied: []};
            window.roomPanel = createCollaborationPanel({
              getChatId: () => window.testRoom.chat,
              getProjectId: () => window.testRoom.project,
              isRunning: () => window.testRoom.running,
              onApplied: mode => window.testRoom.applied.push(mode),
              onSaved: () => {}, onPause: async () => {window.testRoom.running = false;},
            });
            window.roomReady = true;
            </script></body></html>""").encode("utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/fixture":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
                return
            if self.path.startswith("/static/"):
                self.path = self.path[len("/static"):]
            super().do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(STATIC)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_switch_modes_public_text_running_lock_and_project_isolation(browser, room_url, tmp_path):
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    errors, saves = [], []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    payload = {"mode": "default", "room": None, "last_room_run": None, "channel": None}
    page.route("**/api/agent-profiles", lambda route: route.fulfill(json={"profiles": [
        {"name": "researcher", "description": "核验资料与证据"}, {"name": "model-worker", "description": "准备模型与输入"},
    ]}))

    def config(route):
        if 'webchat%3Atwo' in route.request.url:
            route.fulfill(json={"mode": "default", "room": None, "channel": None, "last_room_run": None})
            return
        if route.request.method == "PUT":
            value = route.request.post_data_json
            saves.append(value)
            payload["mode"] = value["mode"]
            if "room" in value:
                payload["room"] = value["room"]
        route.fulfill(json=payload)

    page.route("**/api/conversations/*/collaboration", config)
    try:
        page.goto(room_url)
        page.wait_for_function("window.roomReady === true")
        page.locator("#btn-collaboration").click()
        page.wait_for_function("document.querySelector('#collaboration-mode').disabled === false")
        page.locator("#collaboration-mode").select_option("room")
        assert page.locator("#collaboration-members input:checked").count() == 2
        assert page.get_by_role("button", name="上移 researcher", exact=True).is_disabled()
        assert page.get_by_role("button", name="下移 model-worker", exact=True).is_disabled()
        page.get_by_role("button", name="上移 model-worker", exact=True).click()
        assert page.locator("#collaboration-members input").evaluate_all("inputs => inputs.map(input => input.value)") == ["model-worker", "researcher"]
        assert page.locator(".room-member-order").all_text_contents() == ["1", "2"]
        # Polling must leave unsaved ordering and selection edits intact.
        page.evaluate("async () => {await roomPanel.refresh();}")
        assert page.locator("#collaboration-members input").first.input_value() == "model-worker"
        page.locator('#collaboration-members input[value="model-worker"]').uncheck()
        assert page.locator(".room-member-order").all_text_contents() == ["—", "1"]
        page.locator('#collaboration-members input[value="model-worker"]').check()
        page.locator("#collaboration-concurrency").fill("2")
        page.locator("#collaboration-save").click()
        page.wait_for_function("document.querySelector('#collaboration-message').textContent.includes('已启用公共频道')")
        assert saves[-1]["room"]["members"] == ["model-worker", "researcher"]
        assert saves[-1]["room"]["max_concurrency"] == 2
        assert page.locator("#btn-collaboration").inner_text() == "协作 · 公共频道"
        page.locator("#collaboration-close").click()
        page.locator("#btn-collaboration").click()
        page.wait_for_function("document.querySelector('#collaboration-message').textContent.includes('选择协作方式并保存')")
        assert page.locator("#collaboration-members input").evaluate_all("inputs => inputs.map(input => input.value)") == ["model-worker", "researcher"]
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="下移 model-worker", exact=True).scroll_into_view_if_needed()
        page.get_by_role("button", name="下移 model-worker", exact=True).click()
        page.get_by_role("button", name="上移 model-worker", exact=True).click()
        assert page.locator("#collaboration-members input").first.input_value() == "model-worker"
        page.screenshot(path=str(tmp_path / "public-room-order-mobile.png"))
        page.set_viewport_size({"width": 1200, "height": 900})
        page.evaluate("""() => roomPanel.handleEvent({run_id:'one',event_id:1,kind:'message',message:{message_id:'one:1',sequence:1,author:'researcher',content:'<img src=x onerror=alert(1)> evidence'}},testRoom.chat)""")
        assert page.locator("#collaboration-log img").count() == 0
        assert "<img" in page.locator("#collaboration-log").inner_text()
        page.screenshot(path=str(tmp_path / "public-room.png"))
        page.evaluate("() => {testRoom.running=true; roomPanel.updateRunning();}")
        assert page.locator("#collaboration-save").is_disabled()
        assert page.locator("#collaboration-mode").is_disabled()
        assert page.get_by_role("button", name="下移 model-worker", exact=True).is_disabled()
        assert page.get_by_role("button", name="上移 researcher", exact=True).is_disabled()
        assert page.locator("#collaboration-pause").is_enabled()
        page.locator("#collaboration-pause").click()
        page.wait_for_function("document.querySelector('#collaboration-save').disabled === false")
        page.locator("#collaboration-mode").select_option("default")
        page.locator("#collaboration-save").click()
        page.wait_for_function("document.querySelector('#btn-collaboration').textContent === '协作 · 默认委派'")
        assert saves[-1] == {"mode": "default"}
        page.evaluate("async () => {testRoom.chat='webchat:two'; testRoom.project='project:two'; await roomPanel.refresh();}")
        assert "还没有公开消息" in page.locator("#collaboration-log").inner_text()
        assert page.locator("#collaboration-mode").input_value() == "default"
        assert not errors
    finally:
        page.close()
