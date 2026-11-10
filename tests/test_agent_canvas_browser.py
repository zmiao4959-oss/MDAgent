"""Canvas lifecycle regressions using the real vendored X6 renderer.

Runs against an isolated static server and mocked read-only APIs. No model,
project workspace, or running MiniClaw service is used. Requires Playwright
and its Chromium browser; otherwise this optional browser module is skipped.
"""
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
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def canvas_url():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    start = index.index('  <div id="agent-graph-dialog"')
    end = index.index('  <script type="module" src="/static/app.js', start)
    fixture = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="/static/style.css">'
        '<link rel="stylesheet" href="/static/agent-graph.css"></head><body>'
        '<button id="btn-agent-graph">智能体画布</button>'
        + index[start:end]
        + """<script type="module">
          import { createAgentGraphPanel } from '/static/agent-graph.js';
          createAgentGraphPanel({
            getChatId: () => 'webchat:canvas-test',
            getProjectId: () => 'project:canvas-test',
            isRunning: () => false,
            onApplied: () => {},
            onPause: async () => {},
          });
          window.canvasReady = true;
        </script></body></html>"""
    ).encode("utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/fixture":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(fixture)
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


@pytest.mark.parametrize("saved_graph", [
    None,
    {
        "version": 1,
        "nodes": [{"id": "coordinator", "x": 70, "y": 70}, {"id": "researcher", "x": 350, "y": 70}],
        "edges": [{"source": "coordinator", "target": "researcher"}],
        "entry": "coordinator", "max_messages": 24,
    },
], ids=["default-node", "saved-team"])
def test_reopening_canvas_does_not_accumulate_node_or_edge_views(browser, canvas_url, saved_graph):
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/agent-profiles", lambda route: route.fulfill(json={
        "default_profile": "coordinator",
        "profiles": [{"name": name, "description": name, "tools": [], "skills": []} for name in ("coordinator", "researcher")],
    }))
    page.route("**/api/conversations/*/agent-graph", lambda route: route.fulfill(json={"graph": saved_graph, "last_run": None}))
    try:
        page.goto(canvas_url)
        page.wait_for_function("window.canvasReady === true")
        expected_nodes = [node["id"] for node in saved_graph["nodes"]] if saved_graph else ["coordinator"]
        expected_edges = len(saved_graph["edges"]) if saved_graph else 0
        for opening in range(10):
            page.locator("#btn-agent-graph").click()
            page.wait_for_function("document.querySelector('#agent-graph-message').textContent.includes('单向箭头')")
            # Include async renderer jobs; checking only the model misses orphan views.
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            node_ids = page.locator("#agent-graph-canvas .x6-node").evaluate_all("nodes => nodes.map(node => node.dataset.cellId)")
            assert sorted(node_ids) == sorted(expected_nodes), f"opening {opening + 1}: {node_ids}"
            assert page.locator("#agent-graph-canvas .x6-edge").count() == expected_edges
            assert page.locator("#agent-graph-entry option").count() == len(expected_nodes)
            assert page.locator("#agent-graph-concurrency").input_value() == "4"
            page.locator("#agent-graph-close").click()
        assert not errors
    finally:
        page.close()


def test_parallel_replies_keep_each_nodes_output_separate(browser, canvas_url):
    page = browser.new_page()
    try:
        page.goto(canvas_url)
        page.wait_for_function("window.canvasReady === true")
        page.evaluate("""async () => {
          const {createGraphReplies} = await import('/static/agent-graph.js');
          const container = document.createElement('div');
          container.id = 'test-replies';
          document.body.append(container);
          window.replies = createGraphReplies(container, text => text);
          for (const node of ['worker', 'researcher']) {
            window.replies.handleEvent({kind: 'message', delivery: 'queued', target: node, message_id: node});
          }
        }""")
        cards = page.locator("#test-replies .graph-reply")
        assert cards.locator(".graph-reply-status").all_text_contents() == ["排队中", "排队中"]
        page.evaluate("""() => {
          for (const node of ['worker', 'researcher']) {
            window.replies.handleEvent({kind: 'node_started', node, message_id: node, message_ids: [node]});
          }
          window.replies.handleOutput({node: 'worker', message_id: 'worker', delta: 'work '});
          window.replies.handleOutput({node: 'researcher', message_id: 'researcher', delta: 'research'});
          window.replies.handleOutput({node: 'worker', message_id: 'worker', delta: 'in progress'});
          window.replies.handleOutput({node: 'researcher', message_id: 'researcher', full: 'research complete'});
          window.replies.handleEvent({kind: 'node_done', node: 'researcher', message_id: 'researcher'});
        }""")
        assert cards.locator(".graph-reply-status").all_text_contents() == ["运行中", "完成"]
        assert cards.locator(".graph-reply-body").all_text_contents() == ["work in progress▊", "research complete"]
    finally:
        page.close()


def test_project_skill_is_shown_for_its_owner_and_can_be_revoked(browser, canvas_url):
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    errors, removals = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    name = "project-parameter-helper"
    response = {
        "graph": {"version": 1, "nodes": [{"id": "researcher", "x": 70, "y": 70}], "edges": [], "entry": "researcher", "max_messages": 24},
        "last_run": None,
        "project_skills": {"bindings": {"researcher": {name: {"name": name, "version": "1.0.0", "available": True}}}, "requests": []},
    }
    page.route("**/api/agent-profiles", lambda route: route.fulfill(json={"default_profile": "coordinator", "profiles": [{"name": "researcher", "description": "research", "skills": [], "tools": ["read_skill", "read"]}]}))
    page.route("**/api/conversations/*/agent-graph", lambda route: route.fulfill(json=response))

    def revoke(route):
        removals.append(route.request.method)
        response["project_skills"]["bindings"]["researcher"] = {}
        route.fulfill(json={"status": "revoked"})

    page.route("**/api/conversations/*/project-skills/researcher/*", revoke)
    try:
        page.goto(canvas_url)
        page.wait_for_function("window.canvasReady === true")
        page.locator("#btn-agent-graph").click()
        page.wait_for_function("document.querySelector('#agent-graph-message').textContent.includes('单向箭头')")
        assert name in page.locator("#agent-graph-skills").inner_text()
        page.locator('.x6-node[data-cell-id="researcher"]').click()
        assert name in page.locator("#agent-graph-selection").inner_text()
        page.locator("#agent-graph-skills button").click()
        page.wait_for_function("document.querySelector('#agent-graph-skills .graph-skill') === null")
        assert name not in page.locator("#agent-graph-selection").inner_text()
        assert removals == ["DELETE"]
        assert not errors
    finally:
        page.close()
