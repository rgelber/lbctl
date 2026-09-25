"""End-to-end HTTP tests for lbctl against the fake BIG-IP server.

These exercise the *real* request() path -- auth header, JSON body,
Content-Type, iControl REST selfLink host-stripping, and stats flattening --
against a live (loopback) HTTP server, rather than a stubbed client. They
cover everything the unit tests skip: the full F5Client over the wire, plus
the CLI entrypoint driven through a temp config file.

Run with the rest of the suite (they need the fake server module) or alone::

    python3 -m unittest tests.test_integration -v
    python3 -m pytest tests/test_integration.py -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

lbctl = SourceFileLoader("lbctl", os.path.join(ROOT, "lbctl")).load_module()
fake_f5_server = SourceFileLoader(
    "fake_f5_server", os.path.join(HERE, "fake_f5_server.py")).load_module()


class F5ClientHttpTests(unittest.TestCase):
    """F5Client methods driven over real HTTP to the fake server."""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = fake_f5_server.serve_forever("127.0.0.1:0")

    def setUp(self):
        fake_f5_server.reset()
        self.client = lbctl.F5Client(self.url, "f", "f", verify=False)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_list_partitions(self):
        names = sorted(p["name"] for p in self.client.list_partitions())
        self.assertEqual(names, ["Common", "EXAMPLE"])

    def test_list_pools(self):
        names = sorted(p["name"] for p in self.client.list_pools())
        self.assertEqual(names, ["demo_pool", "fake_pool"])
        example = sorted(p["name"] for p
                         in self.client.list_pools("EXAMPLE"))
        self.assertEqual(example, ["fake_pool"])

    def test_count_and_list_members(self):
        self.assertEqual(self.client.count_pool_members("EXAMPLE", "fake_pool"),
                         2)
        members = self.client.list_pool_members("EXAMPLE", "fake_pool")
        self.assertEqual({m["name"] for m in members}, {
            "~EXAMPLE~web01.example.com:80",
            "~EXAMPLE~web02.example.com:443",
        })

    def test_resolve_member_by_ip(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                            "192.0.2.10")
        self.assertEqual(member["name"], "~EXAMPLE~web01.example.com:80")

    def test_resolve_member_by_node_name_case_insensitive(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                            "WEB01.EXAMPLE.COM")
        self.assertEqual(member["name"], "~EXAMPLE~web01.example.com:80")

    def test_resolve_member_by_name_and_port(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                            "web01.example.com:80")
        self.assertEqual(member["selfLink"],
                         "https://localhost/mgmt/tm/ltm/pool/"
                         "~EXAMPLE~fake_pool/references/members/"
                         "~EXAMPLE~web01.example.com:80?ver=17.1.3")

    def test_resolve_member_none(self):
        self.assertIsNone(self.client.resolve_member("EXAMPLE", "fake_pool",
                                                     "does-not-exist"))

    def test_resolve_member_ambiguous(self):
        # Two members share the node, differing only by port.
        fake_f5_server.reset()
        web01 = fake_f5_server.STATE.find_member("EXAMPLE", "fake_pool",
                                                 "web01.example.com:80")
        self.assertTrue(web01 is not None)
        web01_b = dict(web01)
        web01_b["name"] = "~EXAMPLE~web01.example.com:443"
        web01_b["address"] = "192.0.2.10"
        web01_b["selfLink"] = ("https://localhost/mgmt/tm/ltm/pool/"
                               "~EXAMPLE~fake_pool/references/members/"
                               "~EXAMPLE~web01.example.com:443?ver=17.1.3")
        fake_f5_server.STATE.members["EXAMPLE~fake_pool"].append(web01_b)
        with self.assertRaises(lbctl.F5Error):
            self.client.resolve_member("EXAMPLE", "fake_pool",
                                       "web01.example.com")

    def test_get_state(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                             "web01.example.com")
        state = self.client.get_state(member["selfLink"])
        self.assertEqual(state["session"], "monitor-enabled")
        self.assertEqual(state["state"], "up")

    def test_get_stats_flattens(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                             "web01.example.com")
        fake_f5_server.STATE.active_connections["web01.example.com:80"] = 7
        stats = self.client.get_stats(member["selfLink"])
        self.assertEqual(stats["serverside.curConns"], 7)
        self.assertEqual(stats["curSessions"], 5)
        self.assertEqual(stats["monitorStatus"], "up")
        self.assertEqual(stats["totRequests"], 10)

    def test_set_admin_status_writes_session(self):
        member = self.client.resolve_member("EXAMPLE", "fake_pool",
                                             "web01.example.com")
        self.client.set_admin_status(member["selfLink"], False)
        state = self.client.get_state(member["selfLink"])
        self.assertEqual(state["session"], "user-disabled")
        self.client.set_admin_status(member["selfLink"], True)
        state = self.client.get_state(member["selfLink"])
        self.assertEqual(state["session"], "user-enabled")


class CliHttpTests(unittest.TestCase):
    """The lbctl CLI driven through subprocess with a temp config file."""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = fake_f5_server.serve_forever("127.0.0.1:0")
        # Temp config: --host would also work, but a file exercises the
        # config loader too and keeps creds off the command line.
        cls.config = tempfile.NamedTemporaryFile(
            suffix=".toml", mode="w", delete=False)
        cls.config.write(
            f'host = "{cls.url}"\n'
            'user = "f"\n'
            'password = "f"\n'
            'no_verify = true\n'
            'partition = "EXAMPLE"\n'
            'pool = "fake_pool"\n'
        )
        cls.config.close()

    def setUp(self):
        fake_f5_server.reset()
        self.bin = os.path.join(ROOT, "lbctl")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_lbctl(self, *args, input=None, expect_ok=True):
        result = subprocess.run(
            [sys.executable, self.bin, "-c", self.config.name, *args],
            capture_output=True, text=True,
            input=input)
        if expect_ok:
            self.assertEqual(
                result.returncode, 0,
                msg=f"command {args} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def test_status_lists_all_members(self):
        out = self.run_lbctl("status").stdout
        self.assertIn("web01.example.com", out)
        self.assertIn("web02.example.com", out)

    def test_status_single_member_by_name(self):
        out = self.run_lbctl("status", "--member", "web01.example.com").stdout
        self.assertIn("web01.example.com", out)
        self.assertNotIn("web02.example.com", out)

    def test_list_members(self):
        out = self.run_lbctl("list-members").stdout
        self.assertIn("web01.example.com", out)
        self.assertIn(":80", out)

    def test_list_pools(self):
        out = self.run_lbctl("list-pools").stdout
        self.assertIn("fake_pool", out)

    def test_list_partitions(self):
        out = self.run_lbctl("list-partitions").stdout
        self.assertIn("EXAMPLE", out)

    def test_disable_sets_session_user_disabled(self):
        self.run_lbctl("disable", "--member", "web01.example.com")
        out = self.run_lbctl("status", "--member", "web01.example.com").stdout
        self.assertIn("user-disabled", out)
        # disable does not remove the member from the pool.
        still = self.run_lbctl("list-members").stdout
        self.assertIn("web01.example.com", still)

    def test_enable_sets_session_user_enabled(self):
        self.run_lbctl("disable", "--member", "web01.example.com")
        self.run_lbctl("enable", "--member", "web01.example.com")
        out = self.run_lbctl("status", "--member", "web01.example.com").stdout
        self.assertIn("user-enabled", out)


if __name__ == "__main__":
    unittest.main()