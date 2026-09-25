"""Unit tests for lbctl.

The tool is a single extension-less Python file, imported here via
importlib. Everything that touches the network goes through
F5Client.request(), so the client is stubbed by subclassing and replacing
that one method -- the higher-level logic (resolve_member, get_stats,
list_pools, set_admin_status, ...) is exercised against canned data with no
real HTTP. main()'s argument validation is covered with F5Client patched so
no connection is attempted.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# lbctl is an extension-less file, so use an explicit source loader.
lbctl = SourceFileLoader("lbctl", os.path.join(ROOT, "lbctl")).load_module()


class FakeClient(lbctl.F5Client):
    """A client whose request() is driven by a responder function and
    whose calls are recorded for later assertions."""

    def __init__(self, responder, verbose=False):
        self.responder = responder
        self.calls = []
        self.verbose = verbose

    def request(self, method, path, data=None):
        self.calls.append({"method": method, "path": path, "data": data})
        return self.responder(method, path, data)


# Deliberately fake values: TEST-NET-1 addresses (RFC 5737, reserved for
# documentation) and example.com hostnames that can't collide with real
# infrastructure.
PARTITION = "EXAMPLE"
POOL = "fake_test_pool"

MEMBER = {
    "name": f"~{PARTITION}~web01.example.com:80",
    "address": "192.0.2.10",
    "selfLink": f"https://localhost/mgmt/tm/ltm/pool/"
                f"~{PARTITION}~{POOL}/references/"
                f"members/~{PARTITION}~web01.example.com:80?ver=17.1.3",
}
OTHER = {
    **MEMBER,
    "name": f"~{PARTITION}~web02.example.com:80",
    "address": "192.0.2.11",
    "selfLink": f"https://localhost/mgmt/tm/ltm/pool/"
                f"~{PARTITION}~{POOL}/references/"
                f"members/~{PARTITION}~web02.example.com:80?ver=17.1.3",
}


class StatusBadgeTests(unittest.TestCase):
    def test_good_values(self):
        for v in ("user-enabled", "monitor-enabled", "up", "available"):
            self.assertIn(v, lbctl.status_badge(v))

    def test_bad_values(self):
        for v in ("user-disabled", "down", "offline", "unavailable"):
            self.assertIn(v, lbctl.status_badge(v))

    def test_unknown_is_neither(self):
        out = lbctl.status_badge("something-else")
        self.assertIn("something-else", out)
        # green dot is U+1F7E2, red U+1F534, yellow U+1F7E1 -- unknown is
        # the yellow one and must not be a green/red dot.
        self.assertNotIn("\U0001f7e2", out)
        self.assertNotIn("\U0001f534", out)


class ConfigTests(unittest.TestCase):
    def test_config_format(self):
        self.assertEqual(lbctl.config_format("x.toml"), "toml")
        self.assertEqual(lbctl.config_format("x.json"), "json")
        self.assertEqual(lbctl.config_format("x"), "toml")
        self.assertEqual(lbctl.config_format("x.TOML"), "toml")

    def test_flatten_sections_merges_and_prefers_flat(self):
        data = {
            "connection": {"host": "https://c", "user": "cu"},
            "member": {"pool": "mp", "partition": "EXAMPLE"},
            "host": "https://flat",  # flat key wins over the section copy
        }
        flat = lbctl._flatten_sections(data)
        self.assertEqual(flat["host"], "https://flat")
        self.assertEqual(flat["user"], "cu")
        self.assertEqual(flat["pool"], "mp")
        self.assertEqual(flat["partition"], "EXAMPLE")
        # the section headers themselves must not leak back as dict values
        self.assertNotIn("connection", flat)
        self.assertNotIn("member", flat)

    def test_flatten_sections_flat_still_works(self):
        self.assertEqual(lbctl._flatten_sections({"host": "h"}),
                         {"host": "h"})

    def test_check_config_perms_refuses_group_readable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lbctl.toml")
            with open(path, "w") as fh:
                fh.write('[connection]\nhost = "https://x"\n')
            os.chmod(path, 0o644)
            with self.assertRaises(SystemExit) as ctx:
                lbctl.check_config_perms(path)
            self.assertEqual(ctx.exception.code, 2)

    def test_check_config_perms_ok_when_0600(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lbctl.toml")
            with open(path, "w") as fh:
                fh.write('[connection]\nhost = "https://x"\n')
            os.chmod(path, 0o600)
            lbctl.check_config_perms(path)  # should not raise


class ResolveMemberTests(unittest.TestCase):
    def setUp(self):
        self.items = [MEMBER, OTHER]
        self.client = FakeClient(
            lambda m, p, d: (200, {"items": self.items})
        )

    def test_match_by_ip(self):
        got = self.client.resolve_member("EXAMPLE", "fake_pool",
                                         "192.0.2.10")
        self.assertIs(got, MEMBER)

    def test_match_by_node_name_is_case_insensitive(self):
        got = self.client.resolve_member("EXAMPLE", "fake_pool",
                                         "web01.example.com")
        self.assertIs(got, MEMBER)

    def test_match_by_name_without_port(self):
        got = self.client.resolve_member("EXAMPLE", "fake_pool",
                                         "web01.example.com")
        self.assertIs(got, MEMBER)

    def test_none_match_returns_none(self):
        got = self.client.resolve_member("EXAMPLE", "fake_pool",
                                         "does-not-exist")
        self.assertIsNone(got)

    def test_ambiguous_no_port_raises(self):
        two = [{**MEMBER, "name": "~EXAMPLE~web01.example.com:80"},
               {**MEMBER, "name": "~EXAMPLE~web01.example.com:443",
                "address": "192.0.2.10"}]
        client = FakeClient(
            lambda m, p, d: (200, {"items": two})
        )
        with self.assertRaises(lbctl.F5Error):
            client.resolve_member("EXAMPLE", "p", "web01.example.com")

    def test_port_disambiguates(self):
        two = [{**MEMBER, "name": "~EXAMPLE~web01.example.com:80",
                "selfLink": "A"},
               {**MEMBER, "name": "~EXAMPLE~web01.example.com:443",
                "selfLink": "B", "address": "192.0.2.10"}]
        client = FakeClient(
            lambda m, p, d: (200, {"items": two})
        )
        self.assertEqual(client.resolve_member("EXAMPLE", "p",
                                               "web01.example.com:80")["selfLink"],
                         "A")
        self.assertEqual(client.resolve_member("EXAMPLE", "p",
                                               "web01.example.com:443")["selfLink"],
                         "B")

    def test_request_hits_members_collection(self):
        self.client.resolve_member("EXAMPLE", "fake_pool",
                                   "192.0.2.10")
        self.assertEqual(self.client.calls[0]["method"], "GET")
        self.assertIn("/mgmt/tm/ltm/pool/~EXAMPLE~fake_pool/"
                      "members", self.client.calls[0]["path"])


class ListPartitionsTests(unittest.TestCase):
    def test_only_top_level_folders(self):
        body = {
            "items": [
                {"name": "Common", "fullPath": "/Common"},
                {"name": "EXAMPLE", "fullPath": "/EXAMPLE"},
                {"name": "sub", "fullPath": "/EXAMPLE/sub"},  # nested -> drop
                {"name": "root", "fullPath": "/"},           # root -> drop
            ]
        }
        client = FakeClient(lambda m, p, d: (200, body))
        parts = client.list_partitions()
        self.assertEqual([p["name"] for p in parts], ["Common", "EXAMPLE"])


class ListPoolsTests(unittest.TestCase):
    def setUp(self):
        self.body = {
            "items": [
                {"name": "p1", "partition": "EXAMPLE"},
                {"name": "p2", "partition": "Common"},
                {"name": "p3", "partition": "example"},
            ]
        }

    def test_no_filter_returns_all(self):
        client = FakeClient(lambda m, p, d: (200, self.body))
        self.assertEqual(len(client.list_pools()), 3)

    def test_filter_is_case_insensitive(self):
        client = FakeClient(lambda m, p, d: (200, self.body))
        got = client.list_pools("example")
        self.assertEqual(sorted(p["name"] for p in got), ["p1", "p3"])


class CountAndListMembersTests(unittest.TestCase):
    def setUp(self):
        self.body = {"items": [MEMBER, OTHER]}

    def test_count(self):
        client = FakeClient(lambda m, p, d: (200, self.body))
        self.assertEqual(client.count_pool_members("EXAMPLE", "pool"), 2)

    def test_list_returns_items(self):
        client = FakeClient(lambda m, p, d: (200, self.body))
        self.assertEqual(client.list_pool_members("EXAMPLE", "pool"),
                         self.body["items"])


class GetStatsTests(unittest.TestCase):
    def test_flattens_nested_tmsh(self):
        body = {
            "entries": {
                "0": {
                    "nestedStats": {
                        "entries": {
                            "serverside.curConns": {"value": "0"},
                            "curSessions": {"value": "5"},
                            "monitorStatus": {"description": "up"},
                        }
                    }
                }
            }
        }
        client = FakeClient(lambda m, p, d: (200, body))
        stats = client.get_stats("https://localhost/x")
        self.assertEqual(stats["serverside.curConns"], "0")
        self.assertEqual(stats["curSessions"], "5")
        self.assertEqual(stats["monitorStatus"], "up")

    def test_stats_inserted_before_query_string(self):
        client = FakeClient(lambda m, p, d: (200, {"entries": {}}))
        client.get_stats("https://localhost/m/123?ver=17.1.3")
        path = client.calls[0]["path"]
        self.assertTrue(path.endswith("/stats?ver=17.1.3"))
        self.assertNotIn("/stats?ver", path[:-len("/stats?ver=17.1.3")])


class SetAdminStatusTests(unittest.TestCase):
    def test_enabled_sends_user_enabled_json(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            fake = mock.Mock()
            fake.read.return_value = b"{}"
            fake.status = 200
            urlopen.return_value.__enter__.return_value = fake
            client = lbctl.F5Client("https://bigip.example.com", "u", "p")
            client.set_admin_status(
                "https://localhost/mgmt/tm/ltm/pool/~p~pool/references/"
                "members/~p~web03:443?ver=17.1.3",
                True,
            )
        req = urlopen.call_args[0][0]
        self.assertEqual(json.loads(req.data), {"session": "user-enabled"})
        # Request.add_header() capitalizes the key ("Content-type"), so look
        # the header up case-insensitively.
        ct = next(
            (v for k, v in req.headers.items()
             if k.lower() == "content-type"),
            None,
        )
        self.assertEqual(ct, "application/json")

    def test_disabled_sends_user_disabled(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            fake = mock.Mock()
            fake.read.return_value = b"{}"
            fake.status = 200
            urlopen.return_value.__enter__.return_value = fake
            client = lbctl.F5Client("https://bigip.example.com", "u", "p")
            client.set_admin_status("https://localhost/x", False)
        self.assertEqual(json.loads(urlopen.call_args[0][0].data),
                          {"session": "user-disabled"})


class SelfLinkHostStrippingTests(unittest.TestCase):
    """BIG-IP's selfLink always points at localhost; real requests must hit
    the configured host instead. Verifies the scheme+host stripping and that
    a ?ver= query string is preserved."""

    def test_request_targets_configured_host(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            fake = mock.Mock()
            fake.read.return_value = b"{}"
            fake.status = 200
            urlopen.return_value.__enter__.return_value = fake
            client = lbctl.F5Client("https://bigip.example.com", "u", "p")
            client.get_state("https://localhost/mgmt/tm/ltm/pool/"
                             "~p~pool/references/members/"
                             "~p~web03:443?ver=17.1.3")
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url,
                         "https://bigip.example.com/mgmt/tm/ltm/pool/"
                         "~p~pool/references/members/"
                         "~p~web03:443?ver=17.1.3")
        self.assertNotIn("localhost", req.full_url)


class ResolveFnTests(unittest.TestCase):
    def test_returns_self_link(self):
        client = FakeClient(
            lambda m, p, d: (200, {"items": [MEMBER]})
        )

        class Args:
            pool = "fake_pool"
            partition = "EXAMPLE"
            member = "192.0.2.10"

        self.assertEqual(lbctl.resolve(Args(), client, "status"),
                         MEMBER["selfLink"])

    def test_none_member_raises(self):
        client = FakeClient(lambda m, p, d: (200, {"items": []}))

        class Args:
            pool = "p"
            partition = "EXAMPLE"
            member = "nope"

        with self.assertRaises(lbctl.F5Error):
            lbctl.resolve(Args(), client, "status")


class MainArgValidationTests(unittest.TestCase):
    def setUp(self):
        # keep main from touching a real ~/.lbctl.toml / files
        self.patcher = mock.patch.object(lbctl, "load_config",
                                         return_value={})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        # make sure nothing leaks from the environment
        self._saved = {}
        for key in ("F5_URL", "F5_USER", "F5_PASSWORD"):
            self._saved[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def test_missing_command_returns_2(self):
        self.assertEqual(lbctl.main([]), 2)

    def test_status_without_pool_returns_2(self):
        # list-members/status need --pool; without it, no connection is made
        self.assertEqual(
            lbctl.main(["status", "--member", "web01.example.com"]), 2
        )

    def test_disable_without_member_returns_2(self):
        self.assertEqual(
            lbctl.main(["disable", "--pool", "p"]), 2
        )

    def test_invalid_host_returns_2(self):
        # pool + member present, so it passes the required check and then
        # rejects the non-URL host before any network call
        self.assertEqual(
            lbctl.main([
                "disable",
                "--pool", "fake_pool",
                "--member", "web01.example.com",
                "--host", "ftp://not-a-url",
            ]),
            2,
        )


class CmdStatusIntegrationTests(unittest.TestCase):
    def test_status_prints_member_detail(self):
        class RealClient:
            def __init__(self, *a, **k):
                pass

            def resolve_member(self, partition, pool, member):
                return MEMBER

            def get_state(self, selfLink):
                return {"session": "user-disabled", "state": "up",
                        "monitor": ""}

            def get_stats(self, selfLink):
                return {"monitorStatus": "up",
                        "serverside.curConns": "0",
                        "curSessions": "0",
                        "totRequests": "5"}

        with mock.patch.object(lbctl, "F5Client", RealClient):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = lbctl.main([
                    "status",
                    "--pool", "fake_pool",
                    "--member", "web01.example.com",
                    "--host", "https://bigip.example.com",
                    "--user", "u",
                    "--no-verify",
                    "--password", "p",
                ])

        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("web01.example.com", text)
        self.assertIn("user-disabled", text)
        self.assertIn("curConns", text)
        self.assertIn("totRequests", text)


class VersionTests(unittest.TestCase):
    def test_version_flag(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                lbctl.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(lbctl.__version__, out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)