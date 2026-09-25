"""A fake F5 BIG-IP iControl REST server for testing lbctl end-to-end.

It is intentionally small and lenient -- it only implements the handful of
endpoints lbctl actually hits (folders, pools, pool members, member state,
member stats, member PUT/DELETE) and serves realistic-looking iControl REST
JSON. It is not a faithful BIG-IP: there is no partition ACL, no real health
monitoring, and TLS is off. The point is to give lbctl a real HTTP endpoint
so you can exercise the full request() path -- auth header, JSON body,
Content-Type, selfLink host-stripping, stats flattening -- without a real
BIG-IP.

Run it standalone:

    python3 tests/fake_f5_server.py --port 8443

and point lbctl at it (plain HTTP is fine because lbctl strips the selfLink
host, which always reads as "localhost", and re-wraps it with --host):

    F5_URL=http://127.0.0.1:8443 \
    F5_USER=fake F5_PASSWORD=fake \
    lbctl --partition EXAMPLE --pool fake_pool status

To inject active connections and watch a drain, bump them through the
activeConnections endpoint and watch curConns fall back to 0:

    curl -s -X PUT -d '{"value": 3}' \
      http://127.0.0.1:8443/mgmt/tm/ltm/pool/~EXAMPLE~fake_pool/
      references/members/~EXAMPLE~web01.example.com:80/activeConnections

State (partitions, pools, members) lives in-process, so PUTs made through
lbctl persist for the life of the server process -- disable a member, watch
its session flip to user-disabled in `status`.
"""
import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def parse_ref(s):
    """Split a '~partition~name' iControl reference into (partition, name).

    '~EXAMPLE~fake_pool' -> ('EXAMPLE', 'fake_pool');
    '~EXAMPLE~web01.example.com:80' -> ('EXAMPLE', 'web01.example.com:80').
    """
    name = s.lstrip("~")
    partition, _, rest = name.partition("~")
    return partition, rest


class FakeF5State:
    """Mutable server state shared across requests in one process."""

    def __init__(self):
        self.partitions = [
            {"name": "Common", "fullPath": "/Common", "kind": "sys:folder"},
            {"name": "EXAMPLE", "fullPath": "/EXAMPLE", "kind": "sys:folder"},
        ]
        self.pools = [
            {"name": "fake_pool", "partition": "EXAMPLE",
             "loadBalancingMode": "round-robin", "kind": "ltm:pool"},
            {"name": "demo_pool", "partition": "Common",
             "loadBalancingMode": "round-robin", "kind": "ltm:pool"},
        ]
        # Keyed by "partition~pool" (matching the iControl path style).
        self.members = {
            "EXAMPLE~fake_pool": [
                self._member("EXAMPLE", "fake_pool", "web01.example.com",
                             80, "192.0.2.10"),
                self._member("EXAMPLE", "fake_pool", "web02.example.com",
                             443, "192.0.2.11"),
            ],
            "Common~demo_pool": [
                self._member("Common", "demo_pool", "web03.example.com",
                             8080, "192.0.2.12"),
            ],
        }
        # Active connection counts per member node (e.g. "web01.example.com:80"),
        # used to simulate traffic for drain testing. Defaults to 0.
        self.active_connections = {}

    @staticmethod
    def _member(partition, pool, node, port, address):
        name = f"~{partition}~{node}:{port}"
        return {
            "name": name,
            "address": address,
            "selfLink": (
                "https://localhost/mgmt/tm/ltm/pool/"
                f"~{partition}~{pool}/references/members/{name}"
                "?ver=17.1.3"
            ),
            "session": "monitor-enabled",
            "state": "up",
            "monitor": "/Common/http",
            "connectionLimit": 0,
            "description": "fake lbctl test member",
        }

    def find_pool_members(self, partition, pool):
        return self.members.get(f"{partition}~{pool}", [])

    def find_member(self, partition, pool, node):
        for member in self.find_pool_members(partition, pool):
            _, member_node = parse_ref(member["name"])
            if member_node == node:
                return member
        return None


STATE = FakeF5State()


def reset():
    """Rebuild the in-memory state so tests can start from a clean pool.

    Handlers look up STATE by module-global name at request time, so
    reassigning it here is picked up by an already-running server process.
    Returns the new state.
    """
    global STATE
    STATE = FakeF5State()
    return STATE


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Silence the default per-request access log; the server is a test
    # helper that asserts on responses, not on stderr, so keep the test
    # output clean.
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.route("GET")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return b""
        return self.rfile.read(length)

    def route(self, method):
        parsed = urlparse(self.path)
        segments = [s for s in parsed.path.split("/") if s]
        query = parse_qs(parsed.query)
        body = self._read_body()

        # Only speak iControl REST paths (base is /mgmt/tm/).
        if segments[:2] != ["mgmt", "tm"] or len(segments) < 3 \
                or segments[2] not in ("ltm", "sys"):
            self.send_json(404, {"error": f"unhandled path: {self.path}"})
            return

        try:
            payload = self.dispatch(method, segments, body, query)
            status = payload.get("status", 200)
            result = {k: v for k, v in payload.items() if k != "status"}
            self.send_json(status, result)
        except KeyError as exc:
            self.send_json(404, {"error": f"not found: {exc}"})

    def dispatch(self, method, segments, body, query):
        rest = segments[2:]

        if rest[:2] == ["sys", "folder"]:
            if method != "GET":
                return self._method_not_allowed()
            return {"status": 200, "items": STATE.partitions}

        if rest[0:2] != ["ltm", "pool"]:
            return self._method_not_allowed()

        # /ltm/pool -- list all pools (pool detail is unused by lbctl).
        if len(rest) == 2:
            if method != "GET":
                return self._method_not_allowed()
            return {"status": 200, "items": STATE.pools}

        pool_ref = rest[2]
        partition, pool_name = parse_ref(pool_ref)

        # /ltm/pool/<name>/members -- list members of one pool.
        if len(rest) == 4 and rest[3] == "members":
            if method != "GET":
                return self._method_not_allowed()
            members = STATE.find_pool_members(partition, pool_name)
            return {"status": 200, "items": members}

        # /ltm/pool/<name>/references/members/<member[/<stats|activeConn>]
        # The sub-resource (/stats, /activeConnections) rides on its own
        # segment, so a plain member ref is len(rest) == 6 and a sub-resource
        # call is len(rest) == 7 with that token in rest[6].
        if len(rest) >= 6 and rest[3] == "references" \
                and rest[4] == "members":
            member_ref = rest[5]
            sub = rest[6] if len(rest) >= 7 else ""
            _, member_name = parse_ref(member_ref)
            return self._member_route(method, partition, pool_name,
                                      member_ref, member_name, sub,
                                      body, query)

        return self._method_not_allowed()

    def _member_route(self, method, partition, pool, member_ref,
                      member_name, sub, body, query):
        if sub == "stats":
            return self._member_stats(partition, pool, member_name)
        if sub == "activeConnections":
            if method != "PUT":
                return self._method_not_allowed()
            value = int(json.loads(body).get("value", 0))
            STATE.active_connections[member_name] = value
            return {"status": 200, "members": [member_name],
                    "connections": value}

        # /members/<name> -- member detail (GET/PUT) or delete (DELETE).
        member = STATE.find_member(partition, pool, member_name)
        if member is None:
            return {"status": 404, "error": "member not found"}

        if method == "GET":
            return {"status": 200, **member}
        if method == "PUT":
            session = json.loads(body).get("session")
            member["session"] = session
            return {"status": 200, **member}
        if method == "DELETE":
            STATE.members[f"{partition}~{pool}"].remove(member)
            return {"status": 204}
        return self._method_not_allowed()

    def _member_stats(self, partition, pool, node):
        # Match current BIG-IP firmware, which returns the connection/session
        # counts as JSON numbers (not strings) -- otherwise the tool's
        # wait_drained() would never see curConns reach int 0.
        conns = STATE.active_connections.get(node, 0)
        entries = {
            "serverside.curConns": {"value": conns},
            "curSessions": {"value": 5},
            "monitorStatus": {"description": "up"},
            "totRequests": {"value": 10},
        }
        body = {
            "entries": {
                "0": {"nestedStats": {"entries": entries}},
            }
        }
        return {"status": 200, **body}

    def _method_not_allowed(self):
        return {"status": 405, "error": "method not allowed"}

    def send_json(self, status, obj):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def log_message(self, fmt, *args):
        sys.stderr.write("[fake-f5] " + (fmt % args) + "\n")


def run(host="127.0.0.1", port=8443):
    """Start the fake server and block until interrupted. Returns the server."""
    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        f"[fake-f5] listening on http://{host}:{port} "
        f"(partitions: {', '.join(p['name'] for p in STATE.partitions)}, "
        f"pools: {', '.join(p['name'] for p in STATE.pools)})\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server


def serve_forever(host_url):
    """Context-manager-ish helper for tests: returns (server, host_url).

    Usage in a test:
        server, url = serve_forever("127.0.0.1:0")
        try:
            ...run lbctl against url...
        finally:
            server.shutdown()
    """
    host, _, port = host_url.partition(":")
    port = int(port) if port else 0
    server = ThreadingHTTPServer((host, port), Handler)
    actual_host, actual_port = server.server_address[:2]
    base_url = f"http://{actual_host}:{actual_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base_url


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fake F5 BIG-IP for lbctl tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    run(args.host, args.port)