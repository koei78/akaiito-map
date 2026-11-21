import os
import tempfile
import unittest
import struct
import json
from pathlib import Path

with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as _db_dir:
    os.environ["AKAIITO_DB_PATH"] = os.path.join(_db_dir, "test.sqlite3")
    import app

    class PinTests(unittest.TestCase):
        def test_imported_marker_extent_and_visible_points(self):
            import imported_markers
            with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as folder:
                source = Path(folder) / "markers.json"
                database = Path(folder) / "markers.sqlite3"
                source.write_text(json.dumps({
                    "counts": {"markers": 1, "existing_customers": 1, "parking_spots": 0},
                    "markers": [{"id": "a", "lat": 35.0, "lng": 139.0, "label": "A"}],
                    "existing_customers": [{"id": "b", "lat": 36.0, "lng": 140.0, "label": "B"}],
                    "parking_spots": [],
                }), encoding="utf-8")
                imported_markers.import_json(source, database)
                self.assertEqual(imported_markers.extent(database),
                                 {"south": 35.0, "west": 139.0, "north": 36.0, "east": 140.0})
                self.assertEqual(imported_markers.query((34.0, 138.0, 37.0, 141.0), database)["count"], 2)

        def client(self, name):
            client = app.app.test_client()
            response = client.post("/register", data={"username": name, "password": "strong-password-123"})
            self.assertEqual(response.status_code, 302)
            csrf = client.get("/api/me").json["csrf"]
            return client, {"X-CSRF-Token": csrf}

        def test_private_shared_history_and_permissions(self):
            alice, a = self.client("alice")
            bob, b = self.client("bob")
            body = {"lat": 35.5, "lng": 137.8, "title": "現場", "note": "確認", "shared": False}
            pin = alice.post("/api/pins", json=body, headers=a).json
            pin_id = pin["id"]
            self.assertEqual(bob.get("/api/pins").json, [])
            self.assertEqual(bob.get(f"/api/pins/{pin_id}/history").status_code, 404)
            self.assertEqual(bob.put(f"/api/pins/{pin_id}", json=body, headers=b).status_code, 404)
            self.assertEqual(bob.delete(f"/api/pins/{pin_id}", headers=b).status_code, 404)
            self.assertEqual(alice.put(f"/api/pins/{pin_id}", json={**body, "shared": True}, headers=a).status_code, 200)
            self.assertEqual(len(bob.get("/api/pins").json), 1)
            self.assertEqual(len(bob.get(f"/api/pins/{pin_id}/history").json), 2)
            self.assertEqual(alice.delete(f"/api/pins/{pin_id}", headers=a).status_code, 200)
            self.assertEqual(bob.get("/api/pins").json, [])
            self.assertEqual(bob.get(f"/api/pins/{pin_id}/history").status_code, 404)
            recent = alice.get("/api/pins/history").json
            self.assertEqual([row["action"] for row in recent], ["deleted", "updated", "created"])
            self.assertEqual(bob.get("/api/pins/history").json, [])

        def test_shared_two_person_route_requires_link_and_login(self):
            alice, headers = self.client('routealice')
            bob, _ = self.client('routebob')
            position = {'lat': 35.5, 'lng': 137.8}
            route = {'path': [position], 'visits': [{'position': position, 'number': 1}],
                     'distance': 100, 'duration': 90}
            body = {'origin': position, 'routes': [route, {**route, 'visits': [{'position': position, 'number': 2}]}]}
            self.assertEqual(alice.post('/api/shared-routes', json=body).status_code, 403)
            self.assertEqual(alice.post('/api/shared-routes', json={'routes': []}, headers=headers).status_code, 400)
            response = alice.post('/api/shared-routes', json=body, headers=headers)
            self.assertEqual(response.status_code, 201)
            token = response.json['token']
            self.assertEqual(bob.get('/api/shared-routes/' + token).json, body)
            single = {'origin':position, 'routes':[route]}
            single_response = alice.post('/api/shared-routes', json=single, headers=headers)
            self.assertEqual(single_response.status_code, 201)
            self.assertEqual(bob.get('/api/shared-routes/' + single_response.json['token']).json, single)
            visitor = app.app.test_client()
            self.assertEqual(visitor.get('/api/shared-routes/' + token).status_code, 401)
            visitor.get('/google?shared_route=' + token)
            login = visitor.post('/login', data={'username':'routebob','password':'strong-password-123'})
            self.assertEqual(login.location, '/google?shared_route=' + token)

        def test_route_history_private_share_and_delete(self):
            alice, headers = self.client('historyroutealice')
            bob, bob_headers = self.client('historyroutebob')
            pos = {'lat':35.5, 'lng':137.8}
            route = {'path':[pos], 'visits':[{'position':pos, 'number':1}], 'distance':1, 'duration':1}
            saved = alice.post('/api/shared-routes', json={'origin':pos, 'routes':[route], 'shared':False}, headers=headers)
            token = saved.json['token']
            self.assertEqual(len(alice.get('/api/route-history').json), 1)
            self.assertEqual(bob.get('/api/shared-routes/' + token).status_code, 404)

            self.assertEqual(bob.get('/api/route-history').json, [])
            self.assertEqual(bob.post('/api/route-history/' + token + '/share', headers=bob_headers).status_code, 404)
            self.assertEqual(alice.post('/api/route-history/' + token + '/share', headers=headers).status_code, 200)
            self.assertEqual(bob.get('/api/shared-routes/' + token).status_code, 200)
            self.assertEqual(len(bob.get('/api/route-history').json), 1)
            self.assertEqual(bob.delete('/api/route-history/' + token, headers=bob_headers).status_code, 200)
            self.assertEqual(bob.get('/api/route-history').json, [])
            self.assertEqual(len(alice.get('/api/route-history').json), 1)
            self.assertEqual(alice.delete('/api/route-history/' + token, headers=headers).status_code, 200)
            self.assertEqual(bob.get('/api/shared-routes/' + token).status_code, 404)

        def test_checked_building_persists_per_user_and_can_be_undone(self):
            alice, a = self.client('checkedalice')
            bob, b = self.client('checkedbob')
            key = '35.5000000,137.8000000'
            self.assertEqual(alice.put('/api/checked-buildings', json={'key':key, 'checked':True}, headers=a).status_code, 200)
            self.assertEqual(alice.get('/api/checked-buildings').json, [key])
            self.assertEqual(bob.get('/api/checked-buildings').json, [])
            self.assertEqual(bob.put('/api/checked-buildings', json={'key':key, 'checked':'yes'}, headers=b).status_code, 400)
            self.assertEqual(alice.put('/api/checked-buildings', json={'key':key, 'checked':False}, headers=a).status_code, 200)
            self.assertEqual(alice.get('/api/checked-buildings').json, [])

        def test_shared_pin_display_setting_is_per_account_and_does_not_share_private_pins(self):
            alice, a = self.client("sharedsettingalice")
            bob, b = self.client("sharedsettingbob")
            private = alice.post('/api/pins', json={'lat': 35.5, 'lng': 137.8,
                                  'title': 'private', 'shared': False}, headers=a)
            self.assertEqual(private.status_code, 201)
            self.assertFalse(bob.get('/api/account').json['show_shared_pins'])
            self.assertEqual(bob.patch('/api/account/settings', json={'show_shared_pins': True}, headers=b).status_code, 200)
            self.assertTrue(bob.get('/api/me').json['show_shared_pins'])
            self.assertFalse(alice.get('/api/account').json['show_shared_pins'])
            self.assertEqual(bob.get('/api/pins').json, [])
            self.assertEqual(bob.patch('/api/account/settings', json={'show_shared_pins': 'true'}, headers=b).status_code, 400)
            self.assertEqual(bob.patch('/api/account/settings', json={'show_shared_pins': False}, headers=b).status_code, 200)
            self.assertFalse(bob.get('/api/me').json['show_shared_pins'])

        def test_detected_building_history_is_saved_per_user(self):
            import detection_store
            alice, _ = self.client("detectalice")
            bob, _ = self.client("detectbob")
            alice_id = alice.get("/api/me").json["id"]
            polygon = [[35.5, 137.8], [35.5, 137.81], [35.51, 137.81]]
            run_id = detection_store.start_run(alice_id, 15, polygon)
            points = [
                {"number": 1, "lat": 35.5, "lon": 137.8, "decision": "new", "outline": polygon, "old_date": "2010-01-01", "new_date": "2025-01-01"},
                {"number": 3, "lat": 35.51, "lon": 137.81, "decision": "review", "outline": polygon, "reason": "撮影条件の差"},
                {"number": 2, "lat": 35.6, "lon": 137.9, "decision": "existing", "outline": polygon},
            ]
            detection_store.finish_run(run_id, "done", points, "候補1件")
            listing = alice.get("/api/detections/history").json
            self.assertEqual(listing[0]["building_count"], 1)
            self.assertEqual(listing[0]["review_count"], 1)
            detail = alice.get(f"/api/detections/history/{run_id}").json
            self.assertEqual(detail["polygon"], polygon)
            self.assertEqual(len(detail["points"]), 2)
            self.assertEqual(bob.get("/api/detections/history").json, [])
            self.assertEqual(bob.get(f"/api/detections/history/{run_id}").status_code, 404)
            previous_state = app.state.copy()
            try:
                app.state.update(owner_id=alice_id, status="done", message="候補1件", points=points)
                self.assertEqual(bob.get("/status").json["points"], [])
                self.assertEqual(bob.get("/outlines.geojson").status_code, 404)
                self.assertEqual(alice.get("/status").json["points"], points)
            finally:
                app.state.clear()
                app.state.update(previous_state)

        def test_duplicate_detection_prefers_reconfirmed_result(self):
            outline = [[35.5, 137.8], [35.5, 137.801], [35.501, 137.801], [35.501, 137.8]]
            review = {"decision": "review", "outline": outline, "reason": "要確認"}
            confirmed = {"decision": "new", "outline": outline, "reason": "再確認済み"}
            self.assertEqual(app.deduplicate([review, confirmed])[0]["decision"], "new")

        def test_login_and_csrf(self):
            client, headers = self.client("charlie")
            self.assertEqual(app.app.test_client().get("/health").json, {"ok": True})
            self.assertEqual(client.post("/api/pins", json={}).status_code, 403)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn(b"pins.js", client.get("/").data)
            client.post("/logout", headers=headers)
            self.assertEqual(client.get("/api/pins").status_code, 401)
            self.assertEqual(client.post("/login", data={"username": "charlie", "password": "wrong"}).status_code, 200)
            self.assertEqual(client.post("/login", data={"username": "charlie", "password": "strong-password-123"}).status_code, 302)

        def test_eight_character_password(self):
            client = app.app.test_client()
            self.assertEqual(client.post("/register", data={"username": "shortpw", "password": "1234567"}).status_code, 200)
            self.assertEqual(client.post("/register", data={"username": "shortpw", "password": "12345678"}).status_code, 302)
            self.assertIn(b'minlength="8"', app.app.test_client().get("/register").data)

        def test_map_templates_include_pin_assets(self):
            client, _ = self.client("templateuser")
            page = client.get("/").get_data(as_text=True)
            self.assertIn("/static/pins.css", page)
            self.assertIn("/static/pins.js", page)
            self.assertIn("/static/mobile-map.css", page)
            self.assertIn("/static/mobile-map.js", page)
            self.assertIn("/static/map-theme.css", page)
            self.assertIn("/static/detection-history.js", page)
            with app.app.test_request_context("/google"):
                from flask import render_template, session
                session["csrf"] = "test-token"
                google = render_template("google_map.html", google_key="example")
                self.assertIn("/static/pins.css", google)
                self.assertIn("/static/pins.js", google)
                self.assertIn("/static/mobile-map.css", google)
                self.assertIn("/static/mobile-map.js", google)
                self.assertIn("/static/map-theme.css", google)
                self.assertIn("/static/detection-history.js", google)

        def test_google_map_requires_key_and_opens_when_configured(self):
            from pathlib import Path
            from unittest.mock import patch
            client, _ = self.client("googleuser")
            with patch.dict(os.environ, {"GOOGLE_MAPS_KEY_FILE": str(Path(_db_dir) / "missing-key.txt")}, clear=False):
                self.assertIn("google=missing-key", client.get("/google").location)
            key_file = Path(_db_dir) / "maps-key.txt"
            key_file.write_text("test-key", encoding="utf-8")
            with patch.dict(os.environ, {"GOOGLE_MAPS_KEY_FILE": str(key_file)}, clear=False), patch.object(app, "reserve_map_load", return_value=True):
                self.assertIn("const googleAvailable = true", client.get("/").get_data(as_text=True))
                self.assertIn("maps.googleapis.com", client.get("/google?map=satellite").get_data(as_text=True))
            if os.name == "nt":
                home = Path(_db_dir) / "user"
                fallback = home / "AppData" / "Local" / "AerialComparison" / "google_maps_key.txt"
                fallback.parent.mkdir(parents=True)
                fallback.write_text("fallback-test-key", encoding="utf-8")
                with patch.dict(os.environ, {"GOOGLE_MAPS_KEY_FILE": ""}), patch.object(app, "data_dir", return_value=Path(_db_dir) / "sandbox"), patch.object(app.Path, "home", return_value=home):
                    self.assertEqual(app.google_maps_key(), "fallback-test-key")

        def test_relative_configuration_uses_project_folder(self):
            from unittest.mock import patch
            from paths import ROOT, configured_path, imagery_executable
            with patch.dict(os.environ, {"AKAIITO_IMAGERY_EXE": "tools/imagery.exe"}):
                self.assertEqual(imagery_executable(), ROOT / "tools" / "imagery.exe")
            with patch.dict(os.environ, {"PHOTO_IMAGES_DIR": "photos"}):
                self.assertEqual(configured_path("PHOTO_IMAGES_DIR", ROOT), ROOT / "photos")

        def test_home_screen_assets(self):
            client = app.app.test_client()
            manifest = client.get("/static/manifest.webmanifest")
            self.assertEqual(manifest.status_code, 200)
            self.assertEqual(manifest.json["display"], "standalone")
            manifest.close()
            for size in (192, 512):
                icon = client.get(f"/static/app-icon-{size}.png")
                self.assertEqual(icon.status_code, 200)
                self.assertEqual(struct.unpack(">II", icon.data[16:24]), (size, size))
                icon.close()
            worker = client.get("/sw.js")
            self.assertEqual(worker.status_code, 200)
            worker.close()
            login = client.get("/login").get_data(as_text=True)
            self.assertIn("install-gate.js", login)
            self.assertIn("apple-touch-icon", login)

        def test_account_settings(self):
            client, headers = self.client("accountuser")
            self.assertEqual(client.get("/api/account").json["username"], "accountuser")
            wrong = client.patch("/api/account/username", json={"username":"renameduser","current_password":"wrong"}, headers=headers)
            self.assertEqual(wrong.status_code, 403)
            changed = client.patch("/api/account/username", json={"username":"renameduser","current_password":"strong-password-123"}, headers=headers)
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(client.get("/api/me").json["username"], "renameduser")
            short = client.post("/api/account/password", json={"current_password":"strong-password-123","new_password":"1234567"}, headers=headers)
            self.assertEqual(short.status_code, 400)
            changed = client.post("/api/account/password", json={"current_password":"strong-password-123","new_password":"newpass88"}, headers=headers)
            self.assertEqual(changed.status_code, 200)
            client.post("/logout", headers=headers)
            self.assertEqual(client.post("/login", data={"username":"renameduser","password":"strong-password-123"}).status_code, 200)
            self.assertEqual(client.post("/login", data={"username":"renameduser","password":"newpass88"}).status_code, 302)

    unittest.main()
