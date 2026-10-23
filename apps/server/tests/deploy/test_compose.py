"""compose.yaml: a fixed project name, db unreachable from outside, the
server published on 127.0.0.1 only and trusting X-Forwarded-For from its own
network only, logs capped everywhere, tini as PID 1, rehearsal audio on its
volume, Caddy only in its profile."""

from __future__ import annotations

import asyncio
import ipaddress
import re

import pytest
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from redream_api.core import limits

from .conftest import REPO_ROOT, SERVER_DIR

SUBNET_DEFAULT = re.compile(r"\$\{NETWORK_SUBNET:-([0-9./]+)\}")


def test_every_service_uses_the_logging_anchor(compose: dict) -> None:
    logging = compose["x-logging"]
    assert logging == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    for name, service in compose["services"].items():
        # The same object: an alias of the anchor, not a copy that could drift.
        assert service.get("logging") is logging, name


def test_db_publishes_no_ports(compose: dict) -> None:
    db = compose["services"]["db"]
    assert "ports" not in db
    assert db["image"].startswith("postgres:")
    assert db["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    assert "pg_isready" in db["healthcheck"]["test"]


def test_project_name_is_fixed(compose: dict) -> None:
    # The volumes are redream_pgdata etc. wherever the checkout lives: a
    # renamed or moved checkout must not start on an empty database.
    assert compose["name"] == "redream"


def test_server_is_published_on_127_0_0_1_only(compose: dict) -> None:
    server = compose["services"]["server"]
    assert server["ports"] == ["127.0.0.1:${SERVER_PORT:-8000}:8000"]
    assert "expose" not in server
    assert "network_mode" not in server
    assert "networks" not in server  # the default network, whose subnet is pinned


def test_server_trusts_x_forwarded_for_from_its_network_only(compose: dict) -> None:
    trusted = compose["services"]["server"]["environment"]["FORWARDED_ALLOW_IPS"]
    # Never "*": uvicorn then takes the leftmost entry, which the client writes.
    assert "*" not in trusted
    assert SUBNET_DEFAULT.fullmatch(trusted)
    # The same value as the network's subnet: Caddy and the Docker proxy of the
    # published port connect from inside it.
    assert compose["networks"]["default"]["ipam"]["config"] == [{"subnet": trusted}]
    assert set(compose["networks"]) == {"default"}
    for name, service in compose["services"].items():
        assert "networks" not in service and "network_mode" not in service, name


def _client_key(trusted: str, peer: str, x_forwarded_for: str | None) -> str:
    """The rate-limit key the server computes for one request, through the
    middleware uvicorn runs for --proxy-headers --forwarded-allow-ips."""
    seen: list[str] = []

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        seen.append(limits.client_ip(Request(scope)))

    headers = [] if x_forwarded_for is None else [(b"x-forwarded-for", x_forwarded_for.encode())]
    scope = {"type": "http", "client": (peer, 40000), "headers": headers, "scheme": "http"}
    asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, None, None))  # type: ignore[arg-type]
    return seen[0]


@pytest.mark.parametrize(
    ("peer_offset", "x_forwarded_for", "key"),
    [
        # Caddy replaces the header with the client's address.
        (2, "203.0.113.9", "203.0.113.9"),
        # A proxy or tunnel that appends (Cloudflare, nginx's
        # $proxy_add_x_forwarded_for): what the client wrote comes first, the
        # real address last. The client cannot pick its key.
        (1, "6.6.6.6, 203.0.113.9", "203.0.113.9"),
        (1, "1.1.1.1, 2.2.2.2, 203.0.113.9", "203.0.113.9"),
        (1, "anything-at-all, 203.0.113.9", "203.0.113.9"),
        # No header: the peer itself.
        (1, None, "{peer}"),
    ],
)
def test_the_client_cannot_pick_its_rate_limit_key(
    compose: dict, peer_offset: int, x_forwarded_for: str | None, key: str
) -> None:
    trusted = compose["services"]["server"]["environment"]["FORWARDED_ALLOW_IPS"]
    subnet = SUBNET_DEFAULT.fullmatch(trusted)
    assert subnet
    network = ipaddress.ip_network(subnet.group(1))
    peer = str(network[peer_offset])  # the gateway (Docker proxy) or Caddy
    assert _client_key(subnet.group(1), peer, x_forwarded_for) == key.format(peer=peer)


def test_a_peer_outside_the_network_is_not_trusted(compose: dict) -> None:
    trusted = compose["services"]["server"]["environment"]["FORWARDED_ALLOW_IPS"]
    subnet = SUBNET_DEFAULT.fullmatch(trusted)
    assert subnet
    # e.g. a LAN neighbour routing straight to the container (Docker < 28).
    assert _client_key(subnet.group(1), "192.168.1.50", "6.6.6.6") == "192.168.1.50"


def test_server_runs_the_image_with_init_and_the_audio_volume(compose: dict) -> None:
    server = compose["services"]["server"]
    assert server["build"] == "./apps/server"
    assert server["image"] == "redream-server:local"
    assert server["init"] is True
    assert server["env_file"] == ".env"
    audio_dir = server["environment"]["AUDIO_DIR"]
    assert audio_dir == "/app/data/audio"
    assert f"audio:{audio_dir}" in server["volumes"]
    # The image creates that directory for appuser; a new named volume takes
    # its owner and mode on first use.
    assert f"mkdir -p {audio_dir}" in (SERVER_DIR / "Dockerfile").read_text()
    assert server["depends_on"] == {"db": {"condition": "service_healthy"}}
    database_url = server["environment"]["DATABASE_URL"]
    assert re.fullmatch(
        r"postgresql\+asyncpg://redream:\$\{POSTGRES_PASSWORD[^}]*\}@db:5432/redream", database_url
    )
    # Health: the image's HEALTHCHECK.
    assert "healthcheck" not in server
    # A running chat turn may finish on a stop (Docker's default is 10 s).
    assert server["stop_grace_period"] == "30s"


def test_caddy_is_only_in_the_caddy_profile(compose: dict) -> None:
    services = compose["services"]
    assert services["caddy"]["profiles"] == ["caddy"]
    assert [name for name, s in services.items() if "profiles" in s] == ["caddy"]
    caddy = services["caddy"]
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert caddy["depends_on"] == {"server": {"condition": "service_healthy"}}
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    assert "{$DOMAIN} {" in caddyfile
    assert "reverse_proxy server:8000" in caddyfile
    # caddy fmt's form (tabs), or Caddy warns on every start.
    assert not re.search(r"^ +\S", caddyfile, re.MULTILINE)


def test_caddy_refuses_to_start_without_domain(compose: dict) -> None:
    caddy = compose["services"]["caddy"]
    # Optional for compose (it resolves every service, profile or not, and a
    # required ${DOMAIN:?} would stop plain `docker compose up`) ...
    assert caddy["environment"] == {"DOMAIN": "${DOMAIN:-}"}
    # ... and checked by the container ($$: the container's shell expands it).
    shell, flag, script = caddy["entrypoint"]
    assert (shell, flag) == ("sh", "-c")
    assert script.startswith('test -n "$$DOMAIN" || {')
    assert "exit 1" in script
    assert script.endswith("exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile")


def test_volumes_and_restart(compose: dict) -> None:
    assert set(compose["volumes"]) == {"pgdata", "audio", "caddy_data", "caddy_config"}
    for name, service in compose["services"].items():
        assert service["restart"] == "unless-stopped", name
    assert "version" not in compose


def test_dockerfile_cmd_trusts_proxy_headers_from_forwarded_allow_ips() -> None:
    dockerfile = (SERVER_DIR / "Dockerfile").read_text()
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    assert "--proxy-headers" in cmd
    # Quoted for sh -c (inside the JSON array), so "*" is not globbed.
    assert '--forwarded-allow-ips \\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\\"' in cmd
    assert cmd.index("redream_api.db.migrate") < cmd.index("exec uvicorn")
    assert "--workers 1" in cmd
    assert "--no-access-log" in cmd
