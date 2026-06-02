import http.client
import importlib.util
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401 - silence test HTTP logs
        return


class OpenWorldsStaticRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        self._old_worldos_art_repo_root = os.environ.get("WORLDOS_ART_REPO_ROOT")
        self._old_clawdnd_art_repo_root = os.environ.get("CLAWDND_ART_REPO_ROOT")
        self._old_worldos_repo_root = os.environ.get("WORLDOS_REPO_ROOT")
        self._old_clawdnd_repo_root = os.environ.get("CLAWDND_REPO_ROOT")
        self._old_worldos_player_moves = os.environ.get("WORLDOS_PLAYER_MOVES")
        self._old_clawdnd_player_moves = os.environ.get("CLAWDND_PLAYER_MOVES")
        os.environ.pop("WORLDOS_ART_REPO_ROOT", None)
        os.environ.pop("CLAWDND_ART_REPO_ROOT", None)
        os.environ.pop("WORLDOS_REPO_ROOT", None)
        os.environ.pop("CLAWDND_REPO_ROOT", None)
        os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        os.environ.pop("CLAWDND_PLAYER_MOVES", None)
        self._old_here = server._HERE
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state
        if self._old_worldos_art_repo_root is None:
            os.environ.pop("WORLDOS_ART_REPO_ROOT", None)
        else:
            os.environ["WORLDOS_ART_REPO_ROOT"] = self._old_worldos_art_repo_root
        if self._old_clawdnd_art_repo_root is None:
            os.environ.pop("CLAWDND_ART_REPO_ROOT", None)
        else:
            os.environ["CLAWDND_ART_REPO_ROOT"] = self._old_clawdnd_art_repo_root
        if self._old_worldos_repo_root is None:
            os.environ.pop("WORLDOS_REPO_ROOT", None)
        else:
            os.environ["WORLDOS_REPO_ROOT"] = self._old_worldos_repo_root
        if self._old_clawdnd_repo_root is None:
            os.environ.pop("CLAWDND_REPO_ROOT", None)
        else:
            os.environ["CLAWDND_REPO_ROOT"] = self._old_clawdnd_repo_root
        if self._old_worldos_player_moves is None:
            os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        else:
            os.environ["WORLDOS_PLAYER_MOVES"] = self._old_worldos_player_moves
        if self._old_clawdnd_player_moves is None:
            os.environ.pop("CLAWDND_PLAYER_MOVES", None)
        else:
            os.environ["CLAWDND_PLAYER_MOVES"] = self._old_clawdnd_player_moves
        server._HERE = self._old_here

    def _get(self, path: str) -> tuple[int, str, bytes]:
        status, headers, body = self._get_with_headers(path)
        return status, headers.get("Content-Type", ""), body

    def _get_with_headers(self, path: str) -> tuple[int, http.client.HTTPMessage, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def _status(self, path: str) -> int:
        return self._get(path)[0]

    def test_openworlds_without_trailing_slash_redirects_to_directory_route(self):
        status, headers, body = self._get_with_headers("/openworlds")

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/openworlds/")
        self.assertEqual(body, b"")

    def test_root_and_legacy_routes_redirect_to_openworlds(self):
        for route in ("/", "/index.html", "/legacy", "/legacy.html"):
            with self.subTest(route=route):
                status, headers, body = self._get_with_headers(route)

                self.assertEqual(status, 302)
                self.assertEqual(headers.get("Location"), "/openworlds/")
                self.assertEqual(body, b"")

    def test_deprecated_static_index_redirects_to_openworlds(self):
        source = (server._HERE / "index.html").read_text(encoding="utf-8")

        self.assertIn("Deprecated viewer entry", source)
        self.assertIn("url=/openworlds/", source)
        self.assertIn('window.location.replace("/openworlds/")', source)
        self.assertNotIn('id="grid"', source)
        self.assertNotIn('fetch("/state")', source)

    def test_openworlds_index_uses_local_runtime_assets(self):
        status, ctype, body = self._get("/openworlds/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'vendor/react-18.3.1.development.js', body)
        self.assertIn(b'vendor/react-dom-18.3.1.development.js', body)
        self.assertIn(b'vendor/babel-standalone-7.29.0.min.js', body)
        self.assertIn(b'vendor/google-fonts.css', body)
        self.assertIn(b'native-bridge.js', body)
        self.assertNotIn(b"https://unpkg.com", body)
        self.assertNotIn(b"https://fonts.googleapis.com", body)
        self.assertNotIn(b"tweaks-panel.jsx", body)

    def test_setup_clears_host_repo_and_art_root_env_overrides(self):
        for key in (
            "WORLDOS_ART_REPO_ROOT",
            "CLAWDND_ART_REPO_ROOT",
            "WORLDOS_REPO_ROOT",
            "CLAWDND_REPO_ROOT",
        ):
            self.assertNotIn(key, os.environ)

    def test_openworlds_config_is_browser_safe_metadata(self):
        status, ctype, body = self._get("/openworlds/config.json")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        config = json.loads(body.decode("utf-8"))
        self.assertEqual(config["surface"], "openworlds")
        self.assertEqual(config["state_authority"], "engine")
        self.assertEqual(config["write_lane"], "/move")
        self.assertEqual(config["campaign_catalog"], "/openworlds/campaigns.json")
        self.assertEqual(config["app_status"], "/app-status")
        self.assertFalse(config["demo_data"])
        self.assertTrue(config["demo_data_fallback"])

    def test_openworlds_camp_deep_link_opens_map_camp_mode(self):
        status, ctype, body = self._get("/openworlds/app.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('camp: "map"', source)
        self.assertIn('rest: "map"', source)
        self.assertIn('raw === "camp" || raw === "rest" ? true', source)
        self.assertIn(': false', source)
        self.assertIn('setCampMode(route.campMode)', source)
        self.assertNotIn("typeof route.campMode", source)
        self.assertNotIn("typeof initial.campMode", source)
        self.assertIn('else if (id !== "map") setCampMode(false)', source)
        self.assertIn('location={screen === "map" && campMode ? "Camp"', source)

    def test_openworlds_hash_aliases_match_primary_nav_labels(self):
        # Fresh players and browser-driving agents use the visible top-nav words as hashes.
        # Keep those deep links mapped to the canonical screen ids the nav buttons open.
        status, ctype, body = self._get("/openworlds/app.jsx")
        chrome = (server._OPENWORLDS_DIR / "chrome.jsx").read_text(encoding="utf-8")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('id: "g_party", label: "Party"', chrome)
        self.assertIn('id: "g_worlds", label: "Worlds"', chrome)
        self.assertIn('party: "character"', source)
        self.assertIn('worlds: "launcher"', source)

    def test_merchant_defaults_to_baldurs_gate_lower_city_vendor(self):
        status, ctype, body = self._get("/openworlds/screen-merchant.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('React.useState("old-troutman")', source)
        self.assertIn('id: "old-troutman"', source)
        self.assertIn('waresName: "Old Troutman"', source)
        self.assertIn('location: "Baldur\'s Gate — Lower City"', source)
        self.assertIn('id: "talli"', source)

    def test_merchant_waits_for_live_action_lane_before_purchase(self):
        status, ctype, body = self._get("/openworlds/screen-merchant.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('React.useState("loading")', source)
        self.assertIn('setSurfaceStatus("loading")', source)
        self.assertIn('const surfaceLoading = surfaceStatus === "loading"', source)
        self.assertIn("if (surfaceLoading) return;", source)
        self.assertIn("disabled={cart.length === 0 || surfaceLoading", source)
        self.assertIn("Checking the counter", source)
        self.assertIn("if (!response.ok) throw new Error", source)
        self.assertIn('.catch((e) => toast({ kind: "danger"', source)
        self.assertIn('title: "Move not sent"', source)

    def test_journal_detail_matches_selected_tab_not_first_rumor(self):
        status, ctype, body = self._get("/openworlds/screen-journal.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("function journalQuestInTab(q, tab)", source)
        self.assertIn("const visibleQuests = React.useMemo", source)
        self.assertIn("visibleQuests.find((q) => q.id === activeQuest)", source)
        self.assertIn("visibleQuests[0] || emptyQuest", source)
        self.assertIn("title: \"No active quests\"", source)
        self.assertNotIn("|| quests[0] ||", source)

    def test_app_status_route_exposes_agent_probe_contract(self):
        campaign_dir = self._tmp / "campaigns" / "camp_live"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_live",
                "title": "Live Probe Save",
                "active_session_id": "session_live",
                "world_id": "baldurs-gate",
                "party": ["hero"],
                "characters": {
                    "hero": {
                        "id": "hero",
                        "name": "Probe Hero",
                        "kind": "player",
                        "current_hp": 8,
                        "max_hp": 8,
                    },
                },
            },
        )
        moves = self._tmp / "play-123" / "player_moves.jsonl"
        moves.parent.mkdir()
        moves.write_text("", encoding="utf-8")
        chat = self._tmp / "play-123" / "chat.jsonl"
        chat.write_text('{"role":"dm","text":"Opening."}\n', encoding="utf-8")
        os.environ["CLAWDND_PLAYER_MOVES"] = str(moves)
        _QuietHandler.campaign_id = "camp_live"
        _QuietHandler.chat_path = str(chat)

        status, ctype, body = self._get("/app-status?campaign=camp_live")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["schema"], "worldos.app-status.v1")
        self.assertEqual(payload["state_authority"], "engine")
        self.assertEqual(payload["write_lane"], "/move")
        self.assertEqual(payload["viewer"]["port"], self._port)
        self.assertEqual(payload["viewer"]["chat_lines"], 1)
        self.assertTrue(payload["art"]["private_root"].endswith("content/worlds/_private"))
        self.assertEqual(payload["live"]["attached_campaign_id"], "camp_live")
        self.assertEqual(payload["live"]["campaign_id"], "camp_live")
        self.assertEqual(payload["live"]["active_session_id"], "session_live")
        self.assertEqual(payload["live"]["run_id"], "play-123")
        self.assertEqual(payload["live"]["moves_path"], server._resolved(moves))
        self.assertTrue(payload["live"]["moves_writable"])
        self.assertTrue(payload["live"]["is_live_view"])
        self.assertTrue(payload["live"]["can_act"])
        self.assertEqual(payload["live"]["actor"]["name"], "Probe Hero")
        self.assertIn("continue", payload["live"]["enabled_action_ids"])
        self.assertIn("readiness", payload)
        self.assertIn("health", payload)
        self.assertIn(payload["readiness"]["status"], ("ready", "degraded"))
        self.assertIn("ready_for_smoke", payload["readiness"])
        self.assertIn("ready_for_play", payload["readiness"])
        self.assertTrue(payload["health"]["same_port_alive"])
        self.assertTrue(payload["health"]["route_loaded"])
        self.assertIn("provider_ready", payload["health"])
        self.assertIn("image_probe_ok", payload["health"])
        self.assertIn("failure_bucket", payload["health"])
        self.assertEqual(payload["endpoints"]["session_surface"], "/session-surface")

    def test_app_status_browser_health_counts_console_and_network_logs(self):
        console = self._tmp / "console.ndjson"
        network = self._tmp / "network.ndjson"
        console.write_text(
            "\n".join([
                json.dumps({"type": "warning", "text": "benign"}),
                json.dumps({"type": "pageerror", "text": "Uncaught ReferenceError"}),
            ]) + "\n",
            encoding="utf-8",
        )
        network.write_text(
            "\n".join([
                json.dumps({"status": 200, "url": "/app-status"}),
                json.dumps({"status": 500, "url": "/move"}),
                json.dumps({"error": "requestfailed", "url": "/chat"}),
            ]) + "\n",
            encoding="utf-8",
        )

        self.assertEqual(server._browser_health_counts(str(console), str(network)), (1, 2))

    def test_openworlds_static_assets_are_same_origin_and_local(self):
        status, ctype, body = self._get("/openworlds/vendor/google-fonts.css")

        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(b"font-family: Cinzel", body)
        self.assertIn(b"fonts/", body)
        self.assertNotIn(b"https://", body)

    def test_openworlds_render_bridge_fetches_image_endpoint(self):
        # #W2a: the Img component (chrome.jsx) fetches the viewer /image endpoint and falls
        # back to <Placeholder>; the relations screen uses it for NPC portraits (scope
        # "portrait-<id>"). Proves generated/ingested art renders instead of always-placeholder.
        _s, _c, chrome = self._get("/openworlds/chrome.jsx")
        chrome_src = chrome.decode("utf-8")
        self.assertIn("/image?scope=", chrome_src)
        self.assertIn("function Img(", chrome_src)
        self.assertIn("onError", chrome_src)  # 404 -> graceful placeholder fallback
        _s2, _c2, rel = self._get("/openworlds/screen-relations.jsx")
        rel_src = rel.decode("utf-8")
        self.assertIn("Img scope=", rel_src)
        self.assertIn("portrait-", rel_src)

    def test_provider_launch_replaces_stale_standalone_viewer_history(self):
        # Once a native provider attaches, the previous standalone viewer is stale/read-only.
        # Replacing history keeps Back/navigation drift from disconnecting the player from /move.
        for rel in (
            "screen-launcher.jsx",
            "screen-create.jsx",
            "screen-roster.jsx",
        ):
            source = (server._OPENWORLDS_DIR / rel).read_text(encoding="utf-8")
            self.assertIn("window.location.replace(liveUrl)", source, rel)
            self.assertNotIn("window.location.assign(liveUrl)", source, rel)

    def test_character_screen_window_exports_are_defined(self):
        status, ctype, body = self._get("/openworlds/screen-character.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        export_match = re.search(r"Object\.assign\(window,\s*\{([^}]+)\}\);", source)
        self.assertIsNotNone(export_match)
        exported = [name.strip() for name in export_match.group(1).split(",")]
        definitions = set(re.findall(r"\b(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", source))
        missing = [name for name in exported if name not in definitions]
        self.assertEqual(missing, [])

    def test_ingested_art_root_can_point_at_canonical_private_art_checkout(self):
        canonical = self._tmp / "canonical"
        worktree = self._tmp / "worktree"
        (worktree / "viewer").mkdir(parents=True)
        image_dir = canonical / "content" / "worlds" / "_private" / "baldurs-gate" / "images" / "portrait-gale"
        image_dir.mkdir(parents=True)
        (image_dir / "image.png").write_bytes(b"png")
        (image_dir / "wiki_ingest.json").write_text(
            json.dumps({"path": "/old/machine/path/image.png", "scope": "portrait:gale"}),
            encoding="utf-8",
        )
        os.environ["WORLDOS_ART_REPO_ROOT"] = str(canonical)
        server._HERE = worktree / "viewer"

        self.assertEqual(server._ingested_images_root(), canonical / "content" / "worlds" / "_private")
        desc = server._ingested_descriptor("portrait-gale")

        self.assertIsNotNone(desc)
        self.assertEqual(desc["path"], str(image_dir / "image.png"))

    def test_openworlds_icon_registry_assets_are_local_and_attributed(self):
        index = (server._OPENWORLDS_DIR / "index.html").read_text(encoding="utf-8")
        registry = (server._OPENWORLDS_DIR / "icon-registry.jsx").read_text(encoding="utf-8")
        chrome = (server._OPENWORLDS_DIR / "chrome.jsx").read_text(encoding="utf-8")
        attribution = (server._OPENWORLDS_DIR / "assets" / "icons" / "ATTRIBUTION.md").read_text(encoding="utf-8")

        self.assertIn('src="icon-registry.jsx"', index)
        self.assertLess(index.index('src="icon-registry.jsx"'), index.index('src="chrome.jsx"'))
        self.assertIn("OPENWORLDS_ICON_MANIFEST", registry)
        self.assertNotIn("https://", registry)
        self.assertNotIn('map: "atlas.travel"', registry)
        self.assertIn("CHROME_BUILTIN_GLYPHS", chrome)

        icon_paths = sorted(set(re.findall(r'src: "([^"]+\.svg)"', registry)))
        self.assertGreaterEqual(len(icon_paths), 10)
        for rel in icon_paths:
            icon = server._OPENWORLDS_DIR / rel
            self.assertTrue(icon.exists(), rel)
            self.assertIn(rel.removeprefix("assets/icons/"), attribution)
            svg = icon.read_text(encoding="utf-8")
            self.assertIn("<svg", svg)
            self.assertNotIn('d="M0 0h512v512H0z"', svg)

    def test_openworlds_serves_icon_svgs_with_image_mime_type(self):
        status, ctype, body = self._get("/openworlds/assets/icons/game-icons/lorc/sword-clash.svg")

        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", ctype)
        self.assertIn(b"<svg", body)

    def test_openworlds_shared_buttons_own_their_visible_hit_targets(self):
        # #309: first-minute playability depends on the visible button chrome being clickable,
        # not only the letters/icons inside it. The shared chrome keeps handlers on outer
        # buttons, gives the TabBar button a real flex box, and makes decorative children
        # transparent to pointer targeting so clicks land on the button.
        chrome = (server._OPENWORLDS_DIR / "chrome.jsx").read_text(encoding="utf-8")
        styles = (server._OPENWORLDS_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            chrome,
            r"<button\s+[^>]*type=\"button\"[^>]*key=\{tab\.id\}[^>]*className=\{`tab-button",
        )
        self.assertIn("onClick={() => onNavigate(tab.id)}", chrome)
        self.assertIn(".tab-button", styles)
        tab_button_rule = re.search(r"\.tab-button\s*\{([^}]+)\}", styles, re.S)
        self.assertIsNotNone(tab_button_rule)
        self.assertIn("display: inline-flex", tab_button_rule.group(1))
        self.assertIn("min-height: 34px", tab_button_rule.group(1))
        self.assertIn("button > :where(span, svg, img, .ow-icon, .ow-icon-fallback)", styles)
        self.assertIn("pointer-events: none", styles)

        self.assertIn('className={`nav-item ${currentGroup?.id === g.id ? "active" : ""}`}', chrome)
        self.assertIn("onClick={() => onNavigate(getDefaultScreen(g.id))}", chrome)

    def test_openworlds_agent_driving_hooks_are_stable(self):
        chrome = (server._OPENWORLDS_DIR / "chrome.jsx").read_text(encoding="utf-8")
        launcher = (server._OPENWORLDS_DIR / "screen-launcher.jsx").read_text(encoding="utf-8")
        table = (server._OPENWORLDS_DIR / "screen-table.jsx").read_text(encoding="utf-8")
        settings = (server._OPENWORLDS_DIR / "screen-settings.jsx").read_text(encoding="utf-8")
        toast = (server._OPENWORLDS_DIR / "toast.jsx").read_text(encoding="utf-8")
        character = (server._OPENWORLDS_DIR / "screen-character.jsx").read_text(encoding="utf-8")
        camp = (server._OPENWORLDS_DIR / "camp-sidebar.jsx").read_text(encoding="utf-8")

        for hook in (
            'data-worldos-testid="primary-navigation"',
            'data-worldos-testid="screen-tabs"',
            'data-worldos-testid="screen-tab"',
        ):
            self.assertIn(hook, chrome)
        self.assertIn('role="tablist"', chrome)
        self.assertIn('role="tab"', chrome)
        self.assertIn("aria-selected={current === tab.id}", chrome)

        for hook in (
            'data-worldos-testid="worldos-launcher"',
            'data-worldos-testid="chronicle-start-flow"',
            'testId="chronicle-create-submit"',
            'data-worldos-testid="campaign-row"',
            'data-worldos-testid="error-banner"',
        ):
            self.assertIn(hook, launcher)
        self.assertIn('testId="chronicle-resume"', launcher)
        self.assertIn('testId="chronicle-resume-detail"', launcher)
        self.assertEqual(1, launcher.count('testId="chronicle-resume"'))
        self.assertEqual(1, launcher.count('data-worldos-testid="chronicle-start-flow"'))
        self.assertEqual(1, launcher.count('testId="chronicle-create-submit"'))
        self.assertIn('aria-pressed={selected ? "true" : "false"}', launcher)
        self.assertIn('role="alert"', launcher)

        for hook in (
            'data-worldos-testid="openworlds-root"',
            'data-worldos-testid="app-status-banner"',
            'data-worldos-status-scope="session-surface"',
            'data-worldos-testid="narration-log"',
            'data-worldos-testid="active-player"',
            'data-worldos-testid="action-palette"',
            'data-worldos-testid="action-button"',
            'data-worldos-action-id={actionId || undefined}',
            'data-worldos-testid="move-composer"',
            'data-worldos-testid="move-input"',
        ):
            self.assertIn(hook, table)
        self.assertIn('testId="move-submit"', table)
        self.assertIn('aria-label="Describe your move"', table)
        self.assertIn('aria-live={surfaceStatus === "loading" ? "polite" : "assertive"}', table)
        self.assertIn('aria-label={label}', table)

        for hook in (
            'data-worldos-testid="provider-status"',
            'data-worldos-testid="provider-controls"',
            'data-worldos-testid="provider-card"',
        ):
            self.assertIn(hook, settings)
        self.assertIn('testId="provider-start"', settings)
        self.assertIn('role="status"', settings)

        self.assertIn('data-worldos-testid="toast-region"', toast)
        self.assertIn('role={toast.kind === "danger" ? "alert" : "status"}', toast)
        self.assertIn('data-worldos-testid={toast.kind === "danger" ? "error-banner" : "toast"}', toast)
        self.assertIn('testId="modal-close"', launcher + character)
        self.assertIn('data-worldos-testid="modal-close"', camp)

    def test_launcher_shelf_filters_non_resumable_scratch_runs(self):
        launcher = (server._OPENWORLDS_DIR / "screen-launcher.jsx").read_text(encoding="utf-8")
        app = (server._OPENWORLDS_DIR / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("const playerChronicles = campaigns.filter(isPlayerChronicle);", launcher)
        self.assertIn("function isPlayerChronicle(c)", launcher)
        self.assertIn("return Boolean(c?.canResume || c?.current);", launcher)
        self.assertIn("playerChronicles.length === 0", launcher)
        self.assertIn("playerChronicles.map((c) =>", launcher)
        self.assertNotIn("{campaigns.map((c) =>", launcher)
        self.assertIn("playerChronicles.find((c) => c.live && c.canResume)", launcher)
        self.assertIn("playerChronicles.find((c) => c.canResume)", launcher)
        self.assertIn('display: "flex", flexWrap: "wrap", gap: 10', launcher)
        self.assertIn('style={{ width: 86, textAlign: "center" }}', launcher)
        self.assertIn('w={86} h={104}', launcher)
        self.assertIn("function openWorldsPlayerChronicle(c)", app)
        self.assertIn("const playerCampaigns = nextCampaigns.filter(openWorldsPlayerChronicle);", app)
        self.assertIn("const activeStillExists = playerCampaigns.some((c) => c.id === s?.activeCampaign);", app)
        self.assertIn("playerCampaigns.find((c) => c.current)?.id", app)
        self.assertIn("playerCampaigns.find((c) => c.live && c.canResume)?.id", app)
        self.assertIn("playerCampaigns.find((c) => c.canResume)?.id", app)
        self.assertNotIn("nextCampaigns[0]?.id", app)

    def test_openworlds_title_bar_keeps_title_and_day_pill_apart(self):
        # #306: long campaign titles must never wrap under the nav rail or collide with the
        # day/capability band. The structural fix is intentionally static so the built-app
        # visual proof can focus on real rendering instead of rediscovering this regression.
        chrome = (server._OPENWORLDS_DIR / "chrome.jsx").read_text(encoding="utf-8")
        styles = (server._OPENWORLDS_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: 1fr 240px", styles)
        self.assertIn("paddingLeft: nativeStatus?.bridge ? 76 : 78", chrome)
        self.assertNotIn("paddingLeft: nativeStatus?.bridge ? 76 : 0", chrome)

        title_text_rule = re.search(r"\.title-text\s*\{([^}]+)\}", styles, re.S)
        self.assertIsNotNone(title_text_rule)
        title_text_css = title_text_rule.group(1)
        self.assertIn("white-space: nowrap", title_text_css)
        self.assertIn("overflow: hidden", title_text_css)
        self.assertIn("text-overflow: ellipsis", title_text_css)
        self.assertIn("max-width: calc(100% - 240px)", title_text_css)

        title_end_rule = re.search(r"\.title-end\s*\{([^}]+)\}", styles, re.S)
        self.assertIsNotNone(title_end_rule)
        title_end_css = title_end_rule.group(1)
        self.assertIn("font-size: 13px", title_end_css)
        self.assertIn("min-width: 240px", title_end_css)

    def test_openworlds_combat_screen_binds_viewer_combat_surface(self):
        status, ctype, body = self._get("/openworlds/screen-combat.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('fetch("/combat-surface', source)
        self.assertIn('fetch("/move"', source)
        self.assertIn("window.combatSurfaceFromCampaign", source)
        self.assertNotIn("TOKENS.map", source)
        self.assertNotIn("setTokens", source)

    def test_openworlds_map_screen_binds_viewer_atlas_surface(self):
        status, ctype, body = self._get("/openworlds/screen-map.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('fetch("/atlas-surface', source)
        self.assertIn('fetch("/move"', source)
        self.assertIn("window.atlasSurfaceFromCampaign", source)
        self.assertNotIn("state?.locations", source)

    def test_openworlds_table_posts_only_enabled_session_actions(self):
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("surface?.enabledActions", source)
        self.assertIn("surface?.blockedActions", source)
        self.assertIn("enabledActionById(actionId)", source)
        self.assertIn("fetch(writeLane.endpoint || \"/move\"", source)
        self.assertNotIn("snapshot.json", source)
        self.assertNotIn("writeSnapshot", source)

    def test_openworlds_table_blocks_moves_when_app_status_play_lane_not_ready(self):
        # A static/no-provider viewer can still expose a writable /move file and a can_act surface.
        # The player-facing table must trust same-port /app-status too, otherwise a click lands in
        # "DM composing" forever with no resolver behind it.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("const [appStatus, setAppStatus] = React.useState(null);", source)
        self.assertIn('fetch(`/app-status${query}`', source)
        self.assertIn("const appStatusBlocksPlay = Boolean", source)
        self.assertIn('"no_provider", "no_launcher", "move_rejected"', source)
        self.assertIn("appReadiness.ready_for_play === false", source)
        self.assertIn("Start or resume a provider-backed session from Chronicles", source)
        self.assertIn("data-worldos-status-scope=\"app-status\"", source)
        self.assertIn("appStatusBlocksPlay ? appStatusBlockReason : readOnlyReason", source)
        self.assertIn("disabled={!a.available || pendingActive || appStatusBlocksPlay}", source)
        self.assertIn("disabled={pendingActive || appStatusBlocksPlay}", source)
        self.assertIn("disabled={!actionById(composerMode.actionId)?.available || pendingActive || appStatusBlocksPlay}", source)

    def test_openworlds_table_bounds_and_anchors_the_chronicle(self):
        # #402: the chronicle must stay navigable across a long session — the rendered row count is
        # CAPPED (DOM + a11y tree bounded so the latest beat isn't truncated), the scroll region is
        # labelled role="log" and tracks user scroll, auto-follow respects a reader scrolled up
        # (stick-to-bottom) while a new move snaps to latest, and the action bar is anchored.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        # Rendered window cap (bounds the DOM + accessibility tree).
        self.assertIn("CHRONICLE_RENDER_CAP", source)
        self.assertIn("renderedLog", source)
        self.assertIn("hiddenLogCount", source)
        # The scroll region is a labelled log and reports scroll position for the auto-follow guard.
        self.assertIn('role="log"', source)
        self.assertIn("onLogScroll", source)
        # Auto-follow respects a reader scrolled up, and a new move re-pins to the latest.
        self.assertIn("stickToBottomRef", source)
        self.assertIn("snapNextRef", source)
        # The auto-scroll effect follows the pending/narrating indicator into view too (not just log).
        self.assertIn("}, [renderedLog, pending]);", source)
        # The action bar is explicitly anchored (never pushed out by a growing chronicle).
        self.assertIn('flex: "0 0 auto"', source)

    def test_openworlds_table_promotes_action_palette_into_main_column(self):
        # #G3: the action palette must be PROMINENT in the main play flow, not buried in the
        # 320px right rail. It is rendered in the CENTER column (LEFT — Party / CENTER — Scene
        # + log / RIGHT — Quests), co-located with the free-text Declare box, so a first-time
        # viewer (or a blind AI playtester) sees clickable actions without hunting in a side rail.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        # The three layout-region markers are present and ordered LEFT → CENTER → RIGHT.
        left = source.index("LEFT — Party")
        center = source.index("CENTER — Scene")
        right = source.index("RIGHT — Quests")
        self.assertLess(left, center)
        self.assertLess(center, right)
        # An EncounterButton palette renders inside the CENTER column (between the CENTER and
        # RIGHT markers) — i.e. the palette is in the main column, not only the right rail.
        first_button = source.index("<EncounterButton")
        self.assertGreater(first_button, center)
        self.assertLess(first_button, right)
        # The palette sits with the Declare box (the primary input) in the main action flow.
        self.assertIn(">Actions<", source)
        self.assertIn("DECLARE: free-text action box", source)
        self.assertLess(source.index(">Actions<"), source.index("DECLARE: free-text action box"))

    def test_openworlds_table_action_buttons_select_declare_mode(self):
        # Fresh-player blocker: Say/Do/Check/Save looked clickable but only focused the text box.
        # They now select a visible composer mode, update the placeholder/helper, and Declare posts
        # the selected player-intent kind through the existing /move lane.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("COMPOSER_MODES", source)
        self.assertIn('"palette-skills": "check"', source)
        self.assertIn('"palette-saves": "save"', source)
        self.assertIn("setComposerModeId(nextMode)", source)
        self.assertIn('data-worldos-testid="move-mode"', source)
        self.assertIn("data-worldos-selected", source)
        self.assertIn("kind: composerMode.kind", source)
        self.assertIn("composerMode.placeholder", source)
        self.assertIn("disabled={!actionById(composerMode.actionId)?.available || pendingActive || appStatusBlocksPlay}", source)

    def test_openworlds_table_immediate_actions_reset_stale_composer_mode(self):
        # Fresh-player blocker: if Say was selected, clicking an immediate action like Continue
        # posted the Continue move but left the composer saying Active Abby / Say. Immediate
        # quick actions now reset the free-text composer to its default mode before posting.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        action_move = source.index("if (action.move)")
        reset_mode = source.index('setComposerModeId("do");', action_move)
        clear_input = source.index('setInput("");', action_move)
        post_move = source.index("postMove(action.move, action.label, action.id);", action_move)
        self.assertLess(reset_mode, post_move)
        self.assertLess(clear_input, post_move)

    def test_openworlds_table_renders_all_actions_without_truncation(self):
        # #G3: the palette must not silently cap the action list. The read model emits up to 8
        # verbs (exploration: say/do/check/continue/cast/use + combat: attack/bonus/reaction);
        # the old right-rail `actions.slice(0, 6)` dropped bonus-action + reaction. The palette
        # now splits by group and renders ALL of each group — no slice cap remains.
        status, _ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        source = body.decode("utf-8")
        # No surviving slice that truncates the action list below the full set.
        self.assertNotRegex(source, r"actions\.slice\(\s*0\s*,\s*[0-7]\s*\)")
        # Exploration verbs render always; combat verbs are grouped behind the in-combat gate.
        self.assertIn("explorationActions", source)
        self.assertIn("combatActions", source)
        self.assertIn("actionsInCombat", source)
        self.assertIn("explorationActions.map", source)
        self.assertIn("combatActions.map", source)
        self.assertIn("a.detail || a.groupLabel", source)
        # The grouping keys off the engine-mutated combat gauge (encounter.active / a combat verb
        # being available), never off fiction — keeping the gates/triggers invariant.
        self.assertIn("surface?.encounter?.active", source)
        # The click path is unchanged: the palette still wires through invokeAction.
        self.assertIn("onClick={() => invokeAction(a)}", source)

    def test_openworlds_table_chronicle_preserves_paragraph_breaks(self):
        # #G4: a multi-paragraph DM beat must render as separated paragraphs, not one run-on
        # block. The narration branch renders sanitized {text} in a `div.body`; with the default
        # white-space the embedded blank-line paragraph breaks the DM emits collapse. The render
        # honors them via whiteSpace:"pre-line" (and sanitizeNarration is still applied first).
        # Each narration row should not repeat the region title inline; the surrounding SectionTitle
        # and role="log" label already name the Chronicle.
        status, _ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        source = body.decode("utf-8")
        # The narration div opts into preserving newlines…
        self.assertRegex(source, r'whiteSpace:\s*"pre-line"')
        # …and the GM-advisory strip is still in the narration path (not removed by this change).
        self.assertIn("sanitizeNarration(entry.text)", source)
        self.assertIn('data-worldos-testid="chronicle-narration"', source)
        self.assertNotRegex(source, r'data-worldos-testid="chronicle-narration"[\s\S]*?>Chronicle</span>')

    def test_openworlds_app_bounds_the_live_session_tail(self):
        # #402: the live tail (chatBeats + player echoes) is bounded in useLiveSession so a long
        # session doesn't accumulate state without limit (the upstream half of the DOM-growth fix).
        status, ctype, body = self._get("/openworlds/app.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("MAX_LIVE_BEATS", source)
        self.assertIn("MAX_LIVE_ECHOES", source)
        self.assertIn("boundTail(", source)
        # The cap is applied at the chatBeats append sites and the player-echo append site.
        self.assertIn("boundTail([...prev, ...beats], MAX_LIVE_BEATS)", source)
        self.assertIn("MAX_LIVE_ECHOES", source)

    def test_openworlds_app_honors_campaign_deep_link_once(self):
        # /monitor and /openworlds/campaigns.json cards link to /openworlds/?campaign=<id>.
        # The app must select that catalog entry before falling back to current/live/first,
        # otherwise agents can verify the wrong campaign while thinking the deep link worked.
        status, ctype, body = self._get("/openworlds/app.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("new URLSearchParams(window.location.search || \"\")", source)
        self.assertIn('params.get("campaign")', source)
        self.assertIn("requestedCampaignRef", source)
        self.assertIn("requestedStillExists", source)
        self.assertLess(source.index("requestedStillExists ? requestedCampaign"), source.index("playerCampaigns.find((c) => c.current)?.id"))
        self.assertIn("requestedCampaignRef.current = \"\"", source)

    def test_openworlds_camp_rest_gives_feedback_when_dm_is_busy(self):
        # #402: the Camp "Begin Resting" CTA must give clear feedback when the DM is mid-turn (the
        # bug was a silent no-op — can_act stays true so the click POSTed a move that just queued).
        # ScreenMap threads the DM-busy state from the live session into CampSidebar, which disables
        # the CTA + explains why (and the click handler toasts on the keyboard/edge path).
        _s_map, _c_map, map_body = self._get("/openworlds/screen-map.jsx")
        map_source = map_body.decode("utf-8")
        self.assertIn("liveSession", map_source)
        self.assertIn("dmBusy", map_source)
        self.assertIn("dmBusy={dmBusy}", map_source)

        _s_camp, _c_camp, camp_body = self._get("/openworlds/camp-sidebar.jsx")
        camp_source = camp_body.decode("utf-8")
        self.assertIn("dmBusy", camp_source)
        # The button is disabled while busy, and the early-return path toasts instead of no-op'ing.
        self.assertIn("!canAct || dmBusy", camp_source)
        self.assertIn("still narrating", camp_source)
        # A successful live rest already toasts "Resting"; do not call ScreenMap's atlas-only
        # onBeginRest handler afterward, because that can emit a contradictory "Camp unavailable"
        # toast when the current atlas location is not tagged as a rest point.
        self.assertIn('title: "Resting"', camp_source)
        self.assertNotIn("onBeginRest();", camp_source)

        # And the app actually passes liveSession to the map screen (so dmBusy is real, not always false).
        _s_app, _c_app, app_body = self._get("/openworlds/app.jsx")
        app_source = app_body.decode("utf-8")
        self.assertIn("ScreenMap", app_source)
        self.assertRegex(app_source, r"case \"map\":\s*return <ScreenMap[^>]*liveSession=\{liveSession\}")

    def test_openworlds_acts_screen_binds_viewer_acts_surface(self):
        status, ctype, body = self._get("/openworlds/screen-acts.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('fetch("/acts-surface', source)
        self.assertIn("window.combatSurfaceFromCampaign", source)
        self.assertIn("emptyState", source)

    def test_openworlds_table_and_map_render_calendar_metadata(self):
        for path in ("/openworlds/screen-table.jsx", "/openworlds/screen-map.jsx"):
            with self.subTest(path=path):
                status, ctype, body = self._get(path)

                self.assertEqual(status, 200)
                self.assertIn("text/babel", ctype)
                source = body.decode("utf-8")
                self.assertIn("surface?.calendar?.available", source)
                self.assertIn("calendarMoon", source)
                self.assertIn("calendarDetail", source)

    def test_openworlds_rejects_path_traversal(self):
        self.assertEqual(self._status("/openworlds/../server.py"), 404)
        self.assertEqual(self._status("/openworlds/%2e%2e/server.py"), 404)
        self.assertEqual(self._status("/openworlds/vendor/../../server.py"), 404)

    def test_openworlds_campaigns_projects_current_state_without_private_fields(self):
        campaign_dir = self._tmp / "campaigns" / "camp_live"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_live",
                "title": "Road After Moonrise",
                "ruleset": "SRD 5.2",
                "world_id": "baldurs-gate",
                "day": 12,
                "time_of_day": "dusk",
                "current_location_id": "last-light",
                "summary": "The party reached the inn and caught its breath.",
                "locations": {"last-light": {"name": "Last Light Inn"}},
                "party": ["hero", "jaheira"],
                "characters": {
                    "hero": {"name": "Tav", "kind": "player", "current_hp": 22, "max_hp": 30},
                    "jaheira": {"name": "Jaheira", "kind": "companion", "current_hp": 34, "max_hp": 34},
                },
                "quests": {"q1": {"status": "active"}},
                "notes": "private note must not leave the snapshot",
                "scenes": [{"dm_notes": "hidden agenda"}],
                "lore": ["private canon recall input"],
            },
        )
        (campaign_dir / "sessions").mkdir()
        (campaign_dir / "sessions" / "sess_1.jsonl").write_text("{}", encoding="utf-8")
        _QuietHandler.campaign_id = "camp_live"

        # Isolate the catalog's repo-local roots (play-state/*, qa/state/*) under tmp so
        # a dev worktree that happens to hold local QA runs doesn't leak into the count.
        server._HERE = self._tmp / "viewer"

        status, ctype, body = self._get("/openworlds/campaigns.json")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        catalog = json.loads(body.decode("utf-8"))
        self.assertEqual(catalog["state_authority"], "engine")
        self.assertEqual(catalog["write_lane"], "/move")
        self.assertEqual(catalog["total"], 1)
        campaign = catalog["campaigns"][0]
        self.assertEqual(campaign["id"], "play:state:camp_live")
        self.assertEqual(campaign["campaign_id"], "camp_live")
        self.assertEqual(campaign["source"], "play")
        self.assertEqual(campaign["runId"], "state")
        self.assertEqual(campaign["title"], "Road After Moonrise")
        self.assertEqual(campaign["world"], "baldurs-gate")
        self.assertEqual(campaign["day"], "Day 12 · dusk")
        self.assertEqual(campaign["location"], "Last Light Inn")
        self.assertEqual(campaign["region"], "Last Light Inn")
        self.assertEqual(campaign["system"], "SRD 5.2")
        self.assertEqual(campaign["sessions"], 1)
        self.assertTrue(campaign["current"])
        self.assertTrue(campaign["canResume"])
        self.assertFalse(campaign["readOnly"])
        self.assertEqual(campaign["resumeUrl"], "/openworlds/?campaign=camp_live")
        self.assertEqual(campaign["dashboardUrl"], "/openworlds/?campaign=camp_live")
        self.assertEqual(campaign["legacyDashboardUrl"], "/dashboard?campaign=camp_live")
        self.assertEqual([p["name"] for p in campaign["party"]], ["Tav", "Jaheira"])
        self.assertEqual(campaign["recap"], "The party reached the inn and caught its breath.")
        encoded = json.dumps(campaign)
        self.assertNotIn("private note", encoded)
        self.assertNotIn("hidden agenda", encoded)
        self.assertNotIn("private canon", encoded)
        self.assert_no_private_keys(json.loads(encoded))

    def test_openworlds_campaigns_keeps_current_move_sink_run_live_after_recency_window(self):
        campaign_dir = self._tmp / "campaigns" / "camp_live"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_live",
                "title": "Road After Moonrise",
                "ruleset": "SRD 5.2",
                "world_id": "baldurs-gate",
                "day": 12,
                "current_location_id": "last-light",
                "locations": {"last-light": {"name": "Last Light Inn"}},
                "party": ["hero"],
                "characters": {
                    "hero": {"name": "Tav", "kind": "player", "current_hp": 22, "max_hp": 30},
                },
            },
        )
        stale = 1_700_000_000
        os.utime(campaign_dir / "snapshot.json", (stale, stale))
        moves = self._tmp / "player_moves.jsonl"
        os.environ["CLAWDND_PLAYER_MOVES"] = str(moves)
        _QuietHandler.campaign_id = "camp_live"
        server._HERE = self._tmp / "viewer"
        server._openworlds_catalog_cache = None

        status, _ctype, body = self._get("/openworlds/campaigns.json")

        self.assertEqual(status, 200)
        campaign = json.loads(body.decode("utf-8"))["campaigns"][0]
        self.assertTrue(campaign["current"])
        self.assertTrue(campaign["canResume"])
        self.assertFalse(campaign["readOnly"])
        self.assertTrue(campaign["live"])
        self.assertEqual(campaign["liveStatus"], "live")

    def test_native_start_surfaces_use_app_selected_provider(self):
        app = (Path(__file__).resolve().parents[1] / "openworlds" / "app.jsx").read_text(encoding="utf-8")
        self.assertIn("function nativePreferredProvider(nativeState)", app)
        self.assertIn('return app?.preferences?.selectedProvider || app?.selectedProvider || "";', app)
        self.assertIn("const preferredProvider = nativePreferredProvider(nativeState);", app)
        self.assertIn("preferredProvider={preferredProvider}", app)

        for name in ("screen-launcher.jsx", "screen-create.jsx", "screen-roster.jsx"):
            source = (Path(__file__).resolve().parents[1] / "openworlds" / name).read_text(encoding="utf-8")
            self.assertIn('preferredProvider = ""', source, name)
            self.assertIn("if (preferredProvider) payload.provider = preferredProvider;", source, name)
            self.assertNotIn('provider: "claude"', source, name)
            self.assertNotIn('preferredProvider || "claude"', source, name)

        settings = (Path(__file__).resolve().parents[1] / "openworlds" / "screen-settings.jsx").read_text(encoding="utf-8")
        self.assertIn('const provider = prefs.selectedProvider || app.selectedProvider || "";', settings)
        self.assertIn("if (provider) payload.provider = provider;", settings)
        self.assertNotIn('provider: prefs.selectedProvider || app.selectedProvider || "claude"', settings)

    def test_roster_screen_uses_catalog_campaign_scope(self):
        source = (Path(__file__).resolve().parents[1] / "openworlds" / "screen-roster.jsx").read_text(encoding="utf-8")

        self.assertIn("activeCampaign.campaign_id || campaignId", source)
        self.assertIn('params.set("campaign", rosterCampaignId);', source)
        self.assertIn('params.set("source", activeCampaign.source);', source)
        self.assertIn('params.set("run", activeCampaign.runId);', source)
        self.assertNotIn('params.set("campaign", campaignId);', source)

    def test_monitor_play_campaign_links_openworlds_not_legacy_dashboard(self):
        source = (Path(__file__).resolve().parents[1] / "monitor.html").read_text(encoding="utf-8")

        self.assertIn('href="/openworlds/?campaign=${encodeURIComponent(c.id)}"', source)
        self.assertIn("start one in OpenWorlds", source)
        self.assertNotIn('href="/dashboard?campaign=${encodeURIComponent(c.id)}"', source)
        self.assertNotIn("start one in the dashboard", source)
        self.assertNotIn("the play dashboard", source)

    def test_openworlds_campaigns_includes_repo_play_state_and_qa_runs_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        play_campaign = repo_root / "play-state" / "play-20260525" / "campaigns" / "camp_play"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            play_campaign,
            {
                "id": "camp_play",
                "title": "Owner Save",
                "world_id": "baldurs-gate",
                "current_location_id": "baldurs-gate",
                "locations": {"baldurs-gate": {"name": "Baldur's Gate"}},
                "party": [],
                "characters": {},
            },
        )
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Save",
                "world_id": "baldurs-gate",
                "day": 3,
                "party": [],
                "characters": {},
            },
        )

        status, _ctype, body = self._get("/openworlds/campaigns.json")

        self.assertEqual(status, 200)
        campaigns = json.loads(body.decode("utf-8"))["campaigns"]
        by_id = {c["id"]: c for c in campaigns}
        self.assertIn("play:play-20260525:camp_play", by_id)
        self.assertIn("qa:wave3-red:camp_qa", by_id)
        self.assertEqual(by_id["play:play-20260525:camp_play"]["provider"], "Local")
        self.assertEqual(by_id["qa:wave3-red:camp_qa"]["provider"], "QA")
        self.assertTrue(by_id["play:play-20260525:camp_play"]["readOnly"])
        self.assertTrue(by_id["qa:wave3-red:camp_qa"]["readOnly"])
        self.assertFalse(by_id["play:play-20260525:camp_play"]["canResume"])
        self.assertFalse(by_id["qa:wave3-red:camp_qa"]["canResume"])
        self.assertEqual(by_id["qa:wave3-red:camp_qa"]["monitorUrl"], "/monitor")

    def test_session_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_table"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_table",
                "title": "Table Save",
                "summary": "A quiet table scene.",
                "world_id": "baldurs-gate",
                "current_location_id": "lower-city",
                "locations": {
                    "lower-city": {
                        "name": "Lower City",
                        "description": "Cobbles shine after rain.",
                        "notes": "private route note",
                    },
                },
                "party": ["hero"],
                "characters": {
                    "hero": {
                        "id": "hero",
                        "name": "Tav",
                        "kind": "player",
                        "current_hp": 12,
                        "max_hp": 20,
                        "notes": "private character note",
                    },
                },
                "dm_notes": "hidden route agenda",
            },
        )

        status, ctype, body = self._get("/session-surface?campaign=camp_table")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_table")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["title"], "Table Save")
        self.assertEqual(surface["location"]["name"], "Lower City")
        self.assertEqual(surface["actor"], {"id": "hero", "name": "Tav", "kind": "player"})
        self.assertEqual(surface["party"][0]["name"], "Tav")
        encoded = json.dumps(surface)
        self.assertNotIn("private route", encoded)
        self.assertNotIn("private character", encoded)
        self.assertNotIn("hidden route", encoded)
        self.assert_no_private_keys(surface)

    def test_session_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Table Save",
                "summary": "A QA-only session surface.",
                "world_id": "baldurs-gate",
                "current_location_id": "qa-location",
                "locations": {"qa-location": {"name": "QA Location"}},
                "party": ["hero"],
                "characters": {"hero": {"id": "hero", "name": "QA Tav", "kind": "player"}},
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {
                "id": "camp_live",
                "title": "Live Table Save",
                "party": [],
                "characters": {},
            },
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/session-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Table Save")
        self.assertEqual(surface["location"]["name"], "QA Location")
        self.assertEqual(surface["party"][0]["name"], "QA Tav")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def test_combat_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_combat"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_combat",
                "title": "Combat Save",
                "summary": "A fight at the gate.",
                "current_location_id": "gate",
                "locations": {
                    "gate": {"name": "Basilisk Gate", "notes": "private map bypass"},
                },
                "party": ["hero"],
                "characters": {
                    "hero": {
                        "id": "hero",
                        "name": "Tav",
                        "kind": "player",
                        "current_hp": 12,
                        "max_hp": 20,
                        "armor_class": 16,
                    },
                    "gob": {
                        "id": "gob",
                        "name": "Goblin",
                        "kind": "monster",
                        "armor_class": 13,
                        "notes": "private monster note",
                    },
                },
                "combat": {
                    "active": True,
                    "round": 2,
                    "turn_index": 0,
                    "order": [
                        {"character_id": "hero", "initiative": 18},
                        {"character_id": "gob", "initiative": 9},
                    ],
                },
                "dm_notes": "private route agenda",
            },
        )

        status, ctype, body = self._get("/combat-surface?campaign=camp_combat")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_combat")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["encounter"]["name"], "Basilisk Gate")
        self.assertTrue(surface["encounter"]["active"])
        self.assertEqual([t["id"] for t in surface["tokens"]], ["hero", "gob"])
        self.assertNotIn("ac", surface["tokens"][1])
        encoded = json.dumps(surface)
        self.assertNotIn("private map", encoded)
        self.assertNotIn("private monster", encoded)
        self.assertNotIn("private route", encoded)
        self.assert_no_private_keys(surface)

    def test_combat_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Combat Save",
                "current_location_id": "qa-arena",
                "locations": {"qa-arena": {"name": "QA Arena"}},
                "party": ["hero"],
                "characters": {
                    "hero": {"id": "hero", "name": "QA Tav", "kind": "player"},
                    "gob": {"id": "gob", "name": "QA Goblin", "kind": "monster"},
                },
                "combat": {
                    "active": True,
                    "round": 4,
                    "turn_index": 1,
                    "order": [
                        {"character_id": "hero", "initiative": 18},
                        {"character_id": "gob", "initiative": 9},
                    ],
                },
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {
                "id": "camp_live",
                "title": "Live Combat Save",
                "party": [],
                "characters": {},
            },
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/combat-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Combat Save")
        self.assertEqual(surface["encounter"]["round"], 4)
        self.assertEqual(surface["selectedTokenId"], "gob")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def test_atlas_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_map"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_map",
                "title": "Atlas Save",
                "world_id": "baldurs-gate",
                "current_location_id": "gate",
                "locations": {
                    "gate": {
                        "name": "Basilisk Gate",
                        "connections": ["market", "hidden"],
                        "visited": True,
                        "tags": ["town", "rest"],
                        "notes": "private gate note",
                    },
                    "market": {
                        "name": "Rain Market",
                        "connections": ["gate"],
                        "discovered": True,
                    },
                    "hidden": {
                        "name": "Hidden Crypt",
                        "hidden": True,
                        "notes": "private crypt note",
                    },
                },
                "quests": {
                    "q1": {"title": "Find the Seller", "status": "active", "location_id": "market"},
                },
                "dm_notes": "private atlas agenda",
            },
        )

        status, ctype, body = self._get("/atlas-surface?campaign=camp_map")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_map")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["current_location"]["name"], "Basilisk Gate")
        self.assertEqual([loc["id"] for loc in surface["known_locations"]], ["gate", "market"])
        self.assertEqual(surface["edges"], [{"from": "gate", "to": "market"}])
        self.assertTrue(surface["camp_available"])
        encoded = json.dumps(surface)
        self.assertNotIn("Hidden Crypt", encoded)
        self.assertNotIn("private", encoded)
        self.assert_no_private_keys(surface)

    def test_atlas_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Atlas Save",
                "current_location_id": "qa-gate",
                "locations": {
                    "qa-gate": {"name": "QA Gate", "connections": ["qa-market"], "visited": True},
                    "qa-market": {"name": "QA Market", "visited": True},
                },
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {"id": "camp_live", "title": "Live Atlas Save", "party": [], "characters": {}},
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/atlas-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Atlas Save")
        self.assertEqual(surface["current_location"]["name"], "QA Gate")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def _write_snapshot(self, campaign_dir: Path, payload: dict) -> None:
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "snapshot.json").write_text(json.dumps(payload), encoding="utf-8")

    def assert_no_private_keys(self, value) -> None:
        private_keys = {"notes", "scenes", "lore", "dm_notes", "sealed_agenda", "agenda"}
        if isinstance(value, dict):
            for key, child in value.items():
                self.assertNotIn(key, private_keys)
                self.assert_no_private_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_private_keys(child)


if __name__ == "__main__":
    unittest.main()
