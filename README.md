# lbctl

Gracefully remove, disable, and drain members of a load balancer pool for
maintenance — starting with the F5 BIG-IP iControl REST API. No
third-party dependencies — pure Python 3 standard library.

> **Scope.** lbctl starts with F5 BIG-IP and is being built to work with all
> load balancer systems.

## Why this exists

When you take a node out of a pool the ordinary way, the health monitor can
detect the change as a failure and raise alerts. This tool avoids that by
administratively disabling the member in rotation *before* it is taken out of
service, and by draining active connections first.

The sequence for `disable` is:

1. **Disable** — set the member's `session` to `user-disabled`. The member
   leaves load-balancing rotation. Because it is an administrative state (not a
   health-detected `down`), the monitor does not record a failure.
2. **Drain** — wait for active connections to reach `0` (configurable timeout).

`disable`, `enable`, and `status` cover the individual pieces of that lifecycle.

## Requirements

- macOS or Linux
- Python 3.8+ for core functionality; **Python 3.11+ if you use a TOML
  config file** (uses the stdlib `tomllib`, added in 3.11). JSON config
  files work on any supported Python version.
- Network access to the BIG-IP management interface
- A BIG-IP account with `rest-user` or `admin` role able to modify pool members

There is **no external package to install**. The tool uses only the Python
standard library (`urllib`, `tomllib`), so it works anywhere a supported
Python 3 is available.

## Installation

The tool is a single self-contained executable named `lbctl`.

Copy it somewhere on your `PATH`:

```sh
install -m 0755 lbctl ~/.local/bin/lbctl
```

Or symlink it:

```sh
ln -s "$PWD/lbctl" ~/.local/bin/lbctl
```

Verify it is reachable:

```sh
which lbctl
```

### Makefile install

A `Makefile` wraps the same install for convenience. Install into a prefix:

```sh
make install PREFIX=/usr/local
```

Or stage into a root (used by the RPM spec) so it can be packaged or moved:

```sh
make install DESTDIR=/opt/staged PREFIX=/usr
```

### RPM build & install

The spec in [`packaging/lbctl.spec`](packaging/lbctl.spec) installs the
executable plus the bash and zsh completions. Build the source tarball and RPM:

```sh
make tarball
mkdir -p ~/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS}
cp lbctl-0.2.0.tar.gz ~/rpmbuild/SOURCES/
cp packaging/lbctl.spec ~/rpmbuild/SPECS/
rpmbuild -ba ~/rpmbuild/SPECS/lbctl.spec
# Result: ~/rpmbuild/RPMS/noarch/lbctl-0.2.0-1.noarch.rpm
sudo rpm -i ~/rpmbuild/RPMS/noarch/lbctl-0.2.0-1.noarch.rpm
```

The Makefile is the single source of truth for install paths, so the RPM uses
`make install DESTDIR=%{buildroot}` under the hood.

## Quick start

Authenticate via environment variables (preferred). Password is read from
`F5_PASSWORD`, host from `F5_URL`, user from `F5_USER`:

```sh
export F5_URL="https://bigip-management.example.com"
export F5_USER="admin"
export F5_PASSWORD="s3cret"
```

Inspect a member's current state:

```sh
lbctl -P web_pool -a 10.1.2.3 -t 443 status
```

Disable and drain a member (no removal):

```sh
lbctl -P web_pool -a 10.1.2.3 -t 443 disable
```

Bring a member back into service:

```sh
lbctl -P web_pool -a 10.1.2.3 -t 443 enable
```

## Configuration file

Instead of environment variables, point the tool at a config file with
`-c/--config`. It defaults to `$LBCTL_CONFIG` if set, otherwise
`~/.lbctl.toml`.

**TOML is the default format** (any path without a `.json` extension is
parsed as TOML). Use a `.json` extension to use JSON instead. Group keys
under `[connection]` and `[member]` table headers for readability:

```toml
[connection]
host = "https://bigip-management.example.com"
user = "f5-maint"
password = "s3cret"
```

Create it (it holds a secret, so lock down permissions immediately):

```sh
cat > ~/.lbctl.toml <<'EOF'
[connection]
host = "https://bigip-management.example.com"
user = "f5-maint"
password = "s3cret"
EOF
chmod 600 ~/.lbctl.toml
```

JSON is also supported (use a `.json` config path). JSON has no table-header
syntax, so keys stay flat:

```json
{
  "host": "https://bigip-management.example.com",
  "user": "f5-maint",
  "password": "s3cret"
}
```

Flat top-level TOML keys (no `[connection]`/`[member]` headers) are also
still accepted, for compatibility with older config files:

```toml
host = "https://bigip-management.example.com"
user = "f5-maint"
password = "s3cret"
```

`host` may alternatively be given as `url`. Connection values are resolved
with the following precedence (highest first):

1. command-line flags (`--host`, `--user`, `--password`)
2. config file (`host`/`user`/`password`, flat or under `[connection]`)
3. environment variables (`F5_URL`, `F5_USER`, `F5_PASSWORD`)

The tool refuses to load a config file that is readable by group or others, so
keep it at mode `0600`:

```sh
chmod 600 ~/.lbctl.toml
```

### Creating the config for you

Rather than hand-editing a file, let the tool create one. `-i/--init-config`
writes a TOML template (with `[connection]`/`[member]` headers) to the
config location and exits — no other flags needed:

```sh
lbctl -i
# created config at /Users/you/.lbctl.toml (mode 600) -- edit it with your credentials
```

`-c/--config` points at a specific file; omit it to use the default location.
The default location is `$LBCTL_CONFIG` if that environment variable is
set, otherwise `~/.lbctl.toml`. This lets you point every command at a
different config file just by exporting `LBCTL_CONFIG`. Pointing
`LBCTL_CONFIG` (or `-c`) at a path ending in `.json` generates/reads a
JSON file instead.

### Putting the member identity in the config too

`pool`, `address`, `port`, and `partition` are also accepted in the config
file (flat or under `[member]`), so a fully-populated config needs no other
args at all:

```toml
[connection]
host = "https://bigip-management.example.com"
user = "f5-maint"
password = "s3cret"

[member]
pool = "web_pool"
address = "10.1.2.3"
port = 443
```

```sh
lbctl status
lbctl disable
```

CLI flags still take precedence, so `-P other_pool` overrides the config
value for that one invocation.

### Skipping TLS verification via config

`insecure = true` (alias `no_verify = true`) in the config file has the same
effect as passing `--no-verify` on every invocation — handy for a lab BIG-IP
with a self-signed cert:

```toml
[connection]
host = "https://bigip-management.example.com"
user = "f5-maint"
password = "s3cret"
insecure = true
```

`--no-verify` on the command line still works and overrides the config either
way.

### Where flags go

The connection flags (`-c/--config`, `-H`, `-u`, `-P`, `-a`, `-t`, …) may be
placed either **before** or **after** the subcommand:

```sh
lbctl -c ~/.lbctl.toml -P web_pool -a 10.1.2.3 -t 443 status
lbctl status -c ~/.lbctl.toml -P web_pool -a 10.1.2.3 -t 443
```

The config file is optional — if you supply everything via flags or
environment variables, no config file is needed.

## Identifying a member

A member is identified by three things:

| Flag          | Alias | Example   | Default  | Meaning                          |
|---------------|-------|-----------|----------|----------------------------------|
| `--pool`      | `-P`  | `web_pool`| *(req)*  | Pool name                      |
| `--address`   | `-a`  | `10.1.2.3` or `web03` | *(req)* | Member's IP address **or** its BIG-IP node name |
| `--port`      | `-t`  | `443`     | *(req)*  | Member node port               |
| `--partition` | `-p`  | `app_tenant` | `common` | Partition holding pool + member |

`--address`/`-a` accepts either form because pool members are often created
against a named node (e.g. `web03`) rather than a raw IP — the BIG-IP UI and
your monitoring likely refer to it by name too, so you shouldn't have to look
up the IP just to run a command. Internally, a member's REST name is
`<node-name-or-ip>:<port>` while its `address` field always holds the
resolved IP; `lbctl` matches against whichever one you passed.

To find the exact partition/pool/member values you are allowed to touch, use
the built-in discovery commands instead of hand-rolling `curl` calls:

```sh
# Partitions visible with your credentials.
lbctl list-partitions

# Pools in the default partition (common), or pass -p/--partition for another.
lbctl list-pools

# Pools across every partition you can see, plus member counts.
lbctl list-pools --all-partitions --with-members

# Members of one pool: name/node, address, port, availability, health monitor.
lbctl -P web_pool list-members
```

## Command reference

Common options (apply to every subcommand):

| Option               | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| `--host`, `-H`       | BIG-IP management hostname/IP (or `F5_URL`)                             |
| `--user`, `-u`       | BIG-IP username (or `F5_USER`)                                          |
| `--password`         | Password (falls back to `F5_PASSWORD`, then an interactive prompt)      |
| `-c`, `--config`     | Config file with defaults, TOML by default, `.json` for JSON (default: `$LBCTL_CONFIG` or `~/.lbctl.toml`) |
| `-i`, `--init-config`| Create the config file (with a template) and exit                       |
| `--no-verify`        | Skip TLS certificate verification (BIG-IP ships self-signed certs). Also settable in the config as `insecure = true` |
| `--verbose`, `-v`    | Log every REST call (method, path, response status/timing) to stderr   |
| `--partition`, `-p`  | Partition (default `common`)                                            |
| `--pool`, `-P`       | Pool name (required)                                                    |
| `--address`, `-a`    | Node IP address or BIG-IP node name (required)                          |
| `--port`, `-t`       | Node port (required)                                                    |

`--pool`/`--address`/`--port`/`--partition` are "required" in the sense that
a value must come from *somewhere* — CLI flag or config file (see
[Putting the member identity in the config too](#putting-the-member-identity-in-the-config-too)).

Output is colorized automatically when stdout is a terminal (disable with
`NO_COLOR=1`, force on with `LBCTL_COLOR=always` e.g. for a pager that
preserves ANSI codes).

### `status`

Print the member's `adminStatus`, health `status`, active connection count, and
cumulative sessions. No changes are made.

### `list-partitions` (alias: `partitions`)

List the administrative partitions visible with the configured credentials.
BIG-IP enforces partition ACLs server-side, so this simply reflects what your
account can see — no separate permissions lookup is needed. Unlike the other
commands, it does not need `--pool`/`--address`/`--port`.

```sh
lbctl list-partitions
```

### `list-pools` (alias: `pools`)

List pools visible with the configured credentials, in the partition given by
`--partition`/`-p` (default `common`) unless `--all-partitions` is given. Also
does not need `--pool`/`--address`/`--port`.

| Option                  | Default | Description                                                    |
|--------------------------|---------|------------------------------------------------------------------|
| `--all-partitions`, `-A` | off     | List pools across every visible partition, ignoring `--partition`/`-p` |
| `--with-members`, `-m`   | off     | Also show each pool's member count (one extra REST call per pool) |

```sh
lbctl list-pools
lbctl list-pools --partition app_tenant
lbctl list-pools --all-partitions --with-members
```

### `list-members` (alias: `members`)

List every member of one pool: node name/IP, port, `adminStatus`,
availability `status`, and the health monitor assigned to it. Needs
`--pool`/`-P` but not `--address`/`--port` (it lists everything in the pool).

```sh
lbctl -P web_pool list-members
lbctl -P web_pool -p app_tenant members
```

### `disable`

Sets `adminStatus` to `disabled`, then polls until `activeConnections` is `0`.

| Option          | Default | Description                                                        |
|-----------------|---------|--------------------------------------------------------------------|
| `--max-wait`    | `300`   | Seconds to wait for drain before giving up                         |
| `--poll`        | `5`     | Seconds between drain checks                                       |
| `--force`       | off     | Proceed even if connections remain after `--max-wait`              |
| `--dry-run`, `-n` | off   | Show what would change without making any state-modifying API call |

### `enable`

Sets `adminStatus` back to `enabled` so the member receives traffic again.
Also supports `--dry-run`.

## Authentication and security

- **Prefer a config file or environment variables** so the password never
  appears in `ps` output or shell history. A config file (`~/.lbctl.toml`
  by default) is the most convenient for repeated use; see
  [Configuration file](#configuration-file).
- If no password is supplied, the tool prompts interactively with `getpass`.
- `--password` is supported but prints a warning — it exposes the secret on the
  command line and in your shell history.
- The config file holds a secret. The tool refuses to load it unless it is mode
  `0600`. Keep it that way (`chmod 600 ~/.lbctl.toml`).
- `--no-verify` disables TLS certificate checking. BIG-IP uses self-signed
  certificates by default, so this is often required, but understand it trades
  TLS verification for convenience. Use it over a trusted management network.

## Behavior details

- **Member lookup.** The tool queries the pool's member reference collection
  and matches `--address`/`-a` against either the member's node name or its
  IP address, combined with `--port`/`-t`. It then operates on the member's
  `selfLink`. Using the returned link avoids mistakes guessing the
  `~partition~name` REST path syntax.
- **Drain.** Drain state is read from the member's `activeConnections` field. A
  value of `0` counts as drained.
- **Idempotency.** `disable`/`enable` are safe to re-run.

## Exit codes

| Code | Meaning                                              |
|------|------------------------------------------------------|
| `0`  | Success                                              |
| `1`  | F5/API error (bad auth, member not found, not drained) |
| `2`  | Missing/invalid command-line arguments or no password |

## Relationship to existing tooling

Benefits of this project:

- **Member-level** control (a specific `address:port` in one pool) rather than
  a whole node.
- **Real connection draining** (waits for `activeConnections` to hit `0`)
  instead of a fixed sleep.
- **Full removal** of the member from the pool.
- **No third-party Python package** (this tool is stdlib only).

## Troubleshooting

- **`Cannot reach <host>`** — check the hostname, port, and that the management
  network is reachable. Add `--no-verify` if the failure is a TLS certificate
  error.
- **`member ... not found in pool`** — confirm the pool name, node name/IP,
  and **port** match exactly (use `lbctl -P pool list-members` to see the
  exact values). Verify the `--partition`; a member in another partition
  will not be found under `common`.
- **`N connection(s) still active after Xs`** — real sessions are still in use.
  Wait longer (`--max-wait`), poll more often (`--poll`), or pass `--force` if a
  partial drain is acceptable for this maintenance window.
- **`HTTP 403`** — the account lacks permission to modify the pool member.

## Limitations

- Targets the iControl **REST** API only (BIG-IP 11.6+).
- Drain waits for active connections; it does not wait for persistence
  re-bind or half-open connections beyond the `activeConnections` field.
- Does not modify the health monitor itself. If your monitoring raises alerts on
  the `adminStatus` transition rather than on a probe failure, additional steps
  around the monitor would be required.

## License

Internal use only.