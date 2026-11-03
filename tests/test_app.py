"""End-to-end tests: starts the real server on a free port with a temporary data folder."""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import hrcore


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Client:
    def __init__(self, base): self.base, self.cookie = base, None

    def call(self, path, body=None, method=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if body is not None else "GET"))
        if body is not None: req.add_header("Content-Type", "application/json")
        if self.cookie: req.add_header("Cookie", self.cookie)
        try:
            r = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            r = e
        sc = r.headers.get("Set-Cookie")
        if sc: self.cookie = None if "Max-Age=0" in sc else sc.split(";")[0]
        raw = r.read()
        try: j = json.loads(raw)
        except ValueError: j = raw
        return r.status if hasattr(r, "status") else r.code, j, r.headers


SIGNUP = dict(company="Acme Ltd", industry="Technology", size="11-50", country="UAE", name="Sam Admin",
              email="sam@acme.test", phone="+971 50 123 4567", data_currency="INR", display_currency="AED",
              password="correct horse battery")


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); port = free_port()
        env = dict(os.environ, HR_PORT=str(port), HR_DATA_DIR=cls.tmp.name)
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env, cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try: urllib.request.urlopen(cls.base + "/healthz"); break
            except Exception: time.sleep(.1)

    @classmethod
    def tearDownClass(cls): cls.proc.terminate(); cls.proc.wait(); cls.tmp.cleanup()

    def test_full_flow(self):
        c = Client(self.base)
        self.assertEqual(c.call("/api/me")[0], 401)
        self.assertEqual(c.call("/api/data")[0], 401)
        s, j, h = c.call("/api/signup", dict(SIGNUP, password="short"))
        self.assertEqual(s, 400)
        s, j, h = c.call("/api/signup", SIGNUP)
        self.assertEqual(s, 200); code = j["recovery_code"]
        self.assertRegex(code, r"^[A-Z2-9]{5}(-[A-Z2-9]{5}){3}$")
        self.assertEqual(j["company"]["name"], "Acme Ltd"); self.assertFalse(j["has_data"])
        self.assertEqual(c.call("/api/signup", SIGNUP)[0], 409)            # same email again
        s, j, _ = c.call("/api/me"); self.assertEqual(s, 200)
        # upload: bad file, then good file
        self.assertEqual(c.call("/api/upload", {"csv": "a,b\n1,2\n"})[0], 400)
        s, j, _ = c.call("/api/upload", {"filename": "t.csv", "csv": hrcore.TEMPLATE_CSV}); self.assertEqual(s, 200)
        self.assertEqual(j["report"]["people"], 3)
        s, j, _ = c.call("/api/data"); self.assertEqual(len(j["records"]), 3)
        s, j, _ = c.call("/api/me"); self.assertTrue(j["has_data"])
        # data is encrypted at rest
        raw = open(os.path.join(self.tmp.name, "hr.db"), "rb").read()
        self.assertNotIn(b"Asha Verma", raw)
        # sign out, then sign in
        c.call("/api/logout", {}); self.assertEqual(c.call("/api/me")[0], 401)
        self.assertEqual(c.call("/api/login", {"email": "sam@acme.test", "password": "wrong password!"})[0], 401)
        self.assertEqual(c.call("/api/login", {"email": "SAM@acme.test", "password": SIGNUP["password"]})[0], 200)
        # change password
        self.assertEqual(c.call("/api/password", {"current": "nope nope nope", "new": "another long password"})[0], 403)
        self.assertEqual(c.call("/api/password", {"current": SIGNUP["password"], "new": "another long password"})[0], 200)
        # reset with recovery code
        c2 = Client(self.base)
        self.assertEqual(c2.call("/api/reset", {"email": "sam@acme.test", "recovery_code": "AAAAA-AAAAA-AAAAA-AAAAA", "new_password": "brand new password"})[0], 401)
        s, j, _ = c2.call("/api/reset", {"email": "sam@acme.test", "recovery_code": code.lower(), "new_password": "brand new password"})
        self.assertEqual(s, 200); self.assertNotEqual(j["recovery_code"], code)
        self.assertEqual(c.call("/api/me")[0], 401)                        # old sessions are gone
        self.assertEqual(c2.call("/api/me")[0], 200)
        self.assertEqual(c2.call("/api/reset", {"email": "sam@acme.test", "recovery_code": code, "new_password": "x" * 12})[0], 401)  # old code dead
        # company data is separate per company
        c3 = Client(self.base)
        c3.call("/api/signup", dict(SIGNUP, email="other@beta.test", company="Beta"))
        self.assertEqual(c3.call("/api/data")[1]["records"], [])
        # delete account
        self.assertEqual(c3.call("/api/delete", {"password": "wrong wrong wrong"})[0], 403)
        self.assertEqual(c3.call("/api/delete", {"password": SIGNUP["password"]})[0], 200)
        self.assertEqual(c3.call("/api/me")[0], 401)

    def test_lockout_and_headers(self):
        c = Client(self.base)
        c.call("/api/signup", dict(SIGNUP, email="lock@x.test", company="Lock Co"))
        c.call("/api/logout", {})
        for _ in range(5): c.call("/api/login", {"email": "lock@x.test", "password": "bad bad bad bad"})
        s, j, _ = c.call("/api/login", {"email": "lock@x.test", "password": SIGNUP["password"]})
        self.assertEqual(s, 429)                                          # even the right password is refused while locked
        s, _, h = c.call("/")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"]); self.assertEqual(h["X-Frame-Options"], "DENY")
        s, j, h = Client(self.base).call("/api/signup", dict(SIGNUP, email="c@x.test", company="C Co"))
        self.assertIn("HttpOnly", h["Set-Cookie"]); self.assertIn("SameSite=Strict", h["Set-Cookie"])

    def test_json_only_and_template(self):
        req = urllib.request.Request(self.base + "/api/login", data=b"email=a", method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 415)
        s, j, _ = Client(self.base).call("/api/template.csv"); self.assertIn(b"Date of Joining", j)
        s, j, _ = Client(self.base).call("/api/config"); self.assertIn("AED", j["currencies"]); self.assertTrue(j["demo"])

    def test_real_world_file(self):
        p = os.environ.get("HR_TEST_CSV")
        if not p: self.skipTest("set HR_TEST_CSV to try your own file")
        recs, rep = hrcore.clean_records(open(p, encoding="utf-8-sig").read(), "INR")
        self.assertGreater(len(recs), 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
