"""End-to-end tests of the plugin system against disposable Incus VMs.

Run ``e2e/provision.sh`` first (three Ubuntu VMs with docker, ZFS, NFS, and age),
then ``uv run pytest e2e -v --no-cov``, and ``e2e/teardown.sh`` when done.
compose-farm runs on this machine and reaches the VMs over SSH, like a real
control machine. Compose files reach the VMs through the ``sync`` plugin.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

E2E_DIR = Path(os.environ.get("E2E_DIR", "/tmp/cf-e2e"))
PROJECT = os.environ.get("PROJECT", "cf-e2e")
REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples" / "plugins"
STACKS = E2E_DIR / "stacks"
VMS = ("vm1", "vm2", "vm3")

pytestmark = pytest.mark.skipif(
    not (E2E_DIR / "hosts.env").exists(), reason="run e2e/provision.sh first"
)


def vm(name: str, command: str, *, check: bool = True) -> str:
    """Run a shell command as root inside a VM and return stdout."""
    result = subprocess.run(
        ["incus", "exec", name, "--project", PROJECT, "--", "sh", "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        msg = f"{name}: {command}\n{result.stdout}{result.stderr}"
        raise AssertionError(msg)
    return result.stdout


def running(name: str, stack: str) -> bool:
    return bool(vm(name, f"docker ps -q --filter label=com.docker.compose.project={stack}").strip())


def datasets(name: str, parent: str) -> list[str]:
    return vm(name, f"zfs list -H -o name -r {parent}").split()[1:]


def write_stack(stack: str, compose: str, env: str | None = None) -> None:
    path = STACKS / stack
    path.mkdir(parents=True, exist_ok=True)
    (path / "compose.yaml").write_text(compose)
    if env is not None:
        (path / ".env").write_text(env)


def wait_for(condition: Any, what: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            msg = f"timed out waiting for {what}"
            raise AssertionError(msg)
        time.sleep(0.5)


class Farm:
    """One scenario: its own config and state file, sharing the VMs and compose_dir."""

    def __init__(self, name: str, cf: Path) -> None:
        """Create the scenario directory and read the VM addresses."""
        self.dir = E2E_DIR / "scenarios" / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "compose-farm.yaml"
        self.cf_path = cf
        self.addresses = dict(
            line.split("=", 1) for line in (E2E_DIR / "hosts.env").read_text().split()
        )

    def config(self, stacks: dict[str, str | list[str]], plugins: dict[str, Any]) -> None:
        """Write this scenario's compose-farm.yaml."""
        hosts = {name: {"address": self.addresses[name.upper()], "user": "cf"} for name in VMS}
        data = {"compose_dir": str(STACKS), "hosts": hosts, "stacks": stacks, "plugins": plugins}
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False))

    def cf(self, *args: str, ok: bool = True) -> str:
        """Run cf with this scenario's config; assert its exit status; return its output."""
        env = {
            **os.environ,
            "HOME": str(E2E_DIR / "home"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "250",
        }
        env.pop("SSH_AUTH_SOCK", None)
        env.pop("CF_CONFIG", None)
        result = subprocess.run(
            [str(self.cf_path), *args, "--config", str(self.config_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=900,
            check=False,
        )
        output = result.stdout + result.stderr
        print(f"$ cf {' '.join(args)}\n{output}")
        if ok and result.returncode:
            msg = f"cf {' '.join(args)} failed ({result.returncode}):\n{output}"
            raise AssertionError(msg)
        if not ok and not result.returncode:
            msg = f"cf {' '.join(args)} unexpectedly succeeded:\n{output}"
            raise AssertionError(msg)
        return output


@pytest.fixture(scope="session")
def cf() -> Path:
    """Install compose-farm and the example plugins into a venv, like a user would."""
    venv = E2E_DIR / "venv"
    if not (venv / "bin" / "cf").exists():
        subprocess.run(["uv", "venv", "-q", str(venv)], check=True)
    editable = [arg for path in [REPO, *sorted(EXAMPLES.iterdir())] for arg in ("-e", str(path))]
    subprocess.run(
        ["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"), *editable],
        check=True,
    )
    return venv / "bin" / "cf"


@pytest.fixture(scope="session", autouse=True)
def clean_slate() -> None:
    """Remove containers, datasets, and files left by earlier runs."""
    for name in VMS:
        vm(name, "docker ps -aq | xargs -r docker rm -f", check=False)
        vm(name, f"rm -rf {STACKS} /tmp/cf-before-* /run/agenix/* /root/agenix-src")
        parents = ["tank/data", "tank/shared"] if name == "vm1" else ["tank/data"]  # NFS elsewhere
        for child in [c for parent in parents for c in datasets(name, parent)]:
            vm(name, f"zfs destroy -r {child}", check=False)
    subprocess.run(["rm", "-rf", str(STACKS), str(E2E_DIR / "scenarios")], check=True)


def test_sync_and_commands(cf: Path) -> None:
    """Compose files are rsynced to each host; commands run on the host and locally."""
    farm = Farm("sync", cf)
    write_stack("hello", "services:\n  hello:\n    image: busybox\n    command: sleep infinity\n")
    plugins = {
        "sync": {"excludes": ["*.tmp"]},
        "commands": {
            "before_up": [{"run": "echo before {stack}@{host} > /tmp/cf-before-{stack}"}],
            "after_up": [{"local": f"echo {{stack}}@{{host}} >> {farm.dir}/after.log"}],
            "preflight": [{"run": "command -v docker"}],
        },
    }

    farm.config({"hello": "vm1"}, plugins)
    out = farm.cf("up", "hello")
    assert "Plugins" not in out  # Plugin output only comes from hooks
    assert running("vm1", "hello")
    assert vm("vm1", "cat /tmp/cf-before-hello").strip() == "before hello@vm1"
    assert "busybox" in vm("vm1", f"cat {STACKS}/hello/compose.yaml")

    (STACKS / "hello" / "junk.tmp").write_text("excluded")
    farm.config({"hello": "vm2"}, plugins)
    farm.cf("up", "hello")
    assert running("vm2", "hello")
    assert not running("vm1", "hello")
    assert vm("vm2", f"ls {STACKS}/hello").split() == ["compose.yaml"]
    assert (farm.dir / "after.log").read_text().split() == ["hello@vm1", "hello@vm2"]
    farm.cf("down", "hello")


COUNTER = """\
services:
  counter:
    image: busybox
    command: sh -c 'while true; do date +%s%N >> /data/log; sleep 0.1; done'
    volumes: ["/mnt/data/counter:/data"]
"""


def test_zfs_per_host(cf: Path) -> None:
    """A dataset per stack that moves with the stack without losing writes."""
    farm = Farm("zfs", cf)
    write_stack("counter", COUNTER)
    plugins = {
        "sync": {},
        # Simulate sanoid snapshotting the idle received copy between the two sends
        "commands": {
            "after_source_stopped": [{"run": "sudo zfs snapshot tank/data/{stack}@autosnap-e2e"}]
        },
        "zfs": {"dataset": "tank/data", "zfs": "sudo zfs"},
    }

    # First deploy creates the dataset
    farm.config({"counter": "vm1"}, plugins)
    farm.cf("up", "counter")
    assert datasets("vm1", "tank/data") == ["tank/data/counter"]
    wait_for(lambda: int(vm("vm1", "wc -l < /mnt/data/counter/log")) > 20, "writes on vm1")

    # Migration with `cf up`: live send, final incremental, source retired
    farm.config({"counter": "vm2"}, plugins)
    farm.cf("up", "counter")
    _assert_moved(src="vm1", dst="vm2")

    # Migration back with `cf apply`: the running source must not be stopped as a stray first
    farm.config({"counter": "vm1"}, plugins)
    out = farm.cf("apply")
    assert "Stray stacks to stop" not in out
    _assert_moved(src="vm2", dst="vm1")

    # After `cf down` the state forgets the host; starting elsewhere must not start empty
    farm.cf("down", "counter")
    farm.config({"counter": "vm3"}, plugins)
    out = farm.cf("up", "counter", ok=False)
    assert "tank/data/counter already exists on vm1" in out
    assert datasets("vm3", "tank/data") == []
    assert not running("vm3", "counter")

    # Removing the stack from the config retires its dataset
    farm.config({"counter": "vm1"}, plugins)
    farm.cf("up", "counter")
    farm.config({}, plugins)
    farm.cf("apply")
    assert not running("vm1", "counter")
    assert "tank/data/counter" not in datasets("vm1", "tank/data")
    assert "counter" not in farm.cf("list")


def _assert_moved(src: str, dst: str) -> None:
    assert running(dst, "counter")
    assert not running(src, "counter")
    assert "tank/data/counter" in datasets(dst, "tank/data")
    retired = [d for d in datasets(src, "tank/data") if d.startswith("tank/data/counter.retired-")]
    assert "tank/data/counter" not in datasets(src, "tank/data")
    newest = max(retired)
    # Every write made on the source before it stopped is on the target, and the
    # target kept appending afterwards
    before = vm(src, f"cat /mnt/data/{newest.removeprefix('tank/data/')}/log")
    wait_for(
        lambda: len(vm(dst, "cat /mnt/data/counter/log")) > len(before), f"new writes on {dst}"
    )
    after = vm(dst, "cat /mnt/data/counter/log")
    assert after.startswith(before)
    snapshots = vm(dst, "zfs list -H -o name -t snapshot tank/data/counter").split()
    assert [s for s in snapshots if "@cf-" in s] == [snapshots[-1]]  # Older cf- snapshots pruned
    assert not any("@autosnap-e2e" in s for s in snapshots)  # Empty autosnapshot dropped


SHARED = """\
services:
  shared:
    image: busybox
    command: sh -c 'while true; do date +%s%N >> /data/log; sleep 0.2; done'
    volumes: ["/mnt/shared/shared:/data"]
"""


def test_zfs_storage_host(cf: Path) -> None:
    """All datasets on vm1 (the NAS), shared over NFS: migrations move nothing."""
    farm = Farm("storage", cf)
    write_stack("shared", SHARED)
    plugins = {
        "sync": {},
        "zfs": {"dataset": "tank/shared", "zfs": "sudo zfs", "storage_host": "vm1"},
    }

    farm.config({"shared": "vm2"}, plugins)
    farm.cf("up", "shared")
    assert datasets("vm1", "tank/shared") == ["tank/shared/shared"]
    assert running("vm2", "shared")
    wait_for(lambda: vm("vm1", "cat /mnt/shared/shared/log", check=False), "writes via NFS")

    lines = int(vm("vm1", "wc -l < /mnt/shared/shared/log"))
    farm.config({"shared": "vm3"}, plugins)
    farm.cf("up", "shared")
    assert running("vm3", "shared")
    assert not running("vm2", "shared")
    assert datasets("vm1", "tank/shared") == ["tank/shared/shared"]  # Nothing moved
    wait_for(lambda: int(vm("vm1", "wc -l < /mnt/shared/shared/log")) > lines, "vm3 appending")

    farm.config({}, plugins)
    farm.cf("apply")
    assert not running("vm3", "shared")
    [retired] = datasets("vm1", "tank/shared")
    assert retired.startswith("tank/shared/shared.retired-")


AGENIX_STACK = """\
services:
  app:
    image: busybox
    command: sleep infinity
    environment:
      API_TOKEN: ${API_TOKEN:?API_TOKEN missing}
      DOMAIN: ${DOMAIN:?DOMAIN missing}
    secrets: [admin-token]
secrets:
  admin-token:
    file: /run/agenix/admin-token
"""


def test_agenix(cf: Path) -> None:
    """Secrets decrypted with each host's SSH key reach compose; a host without them is refused."""
    token = f"s3cret-{os.getpid()}"
    _deploy_secrets({"secretapp.env": f"API_TOKEN={token}\n", "admin-token": "hunter2"})
    farm = Farm("agenix", cf)
    write_stack("secretapp", AGENIX_STACK, env="DOMAIN=example.test\n")
    plugins = {
        "sync": {},
        "agenix": {"stacks": {"secretapp": {"env": "secretapp.env", "files": ["admin-token"]}}},
    }

    farm.config({"secretapp": "vm1"}, plugins)
    out = farm.cf("up", "secretapp")
    assert "--env-file .env --env-file /run/agenix/secretapp.env" in out
    assert token not in out
    assert _seen_by_container("vm1") == f"{token} example.test hunter2"

    # vm3 has no secrets: preflight refuses before vm1 is stopped
    farm.config({"secretapp": "vm3"}, plugins)
    out = farm.cf("up", "secretapp", ok=False)
    assert "secret /run/agenix/secretapp.env is missing or unreadable" in out
    assert running("vm1", "secretapp")
    assert not running("vm3", "secretapp")

    farm.config({"secretapp": "vm2"}, plugins)
    farm.cf("up", "secretapp")
    assert not running("vm1", "secretapp")
    assert _seen_by_container("vm2") == f"{token} example.test hunter2"
    farm.cf("down", "secretapp")


def _deploy_secrets(secrets: dict[str, str]) -> None:
    """Encrypt for vm1 and vm2's SSH host keys and decrypt on each, like agenix does."""
    recipients = " ".join(
        f"-r '{vm(name, 'cat /etc/ssh/ssh_host_ed25519_key.pub').strip()}'"
        for name in ("vm1", "vm2")
    )
    src = "/root/agenix-src"  # Not /tmp: protected_regular blocks overwriting pushed files
    for secret, content in secrets.items():
        encrypted = E2E_DIR / f"{secret}.age"
        vm(
            "vm1",
            f"mkdir -p {src} && printf %s '{content}' | age {recipients} -o {src}/{secret}.age",
        )
        _incus_file("pull", f"vm1{src}/{secret}.age", str(encrypted))
        for name in ("vm1", "vm2"):
            vm(name, f"mkdir -p {src}")
            _incus_file("push", str(encrypted), f"{name}{src}/{secret}.age")
            vm(
                name,
                f"mkdir -p /run/agenix && age -d -i /etc/ssh/ssh_host_ed25519_key "
                f"-o /run/agenix/{secret} {src}/{secret}.age && "
                f"chown cf /run/agenix/{secret} && chmod 0400 /run/agenix/{secret}",
            )


def _incus_file(action: str, source: str, target: str) -> None:
    subprocess.run(["incus", "file", action, source, target, "--project", PROJECT], check=True)


def _seen_by_container(name: str) -> str:
    return vm(
        name,
        "docker exec secretapp-app-1 sh -c 'echo $API_TOKEN $DOMAIN $(cat /run/secrets/admin-token)'",
    ).strip()


def test_after_changes_and_pin(cf: Path) -> None:
    """after_changes runs once per command with the changed stacks; pinned stacks don't move."""
    farm = Farm("changes", cf)
    for stack in ("alpha", "beta"):
        write_stack(stack, "services:\n  app:\n    image: busybox\n    command: sleep infinity\n")
    log = farm.dir / "changes.log"
    plugins = {
        "pin": {"alpha": {"host": "vm1", "reason": "e2e pin"}},
        "sync": {},
        "commands": {"after_changes": [{"local": f"echo {{stacks}} >> {log}"}]},
    }

    farm.config({"alpha": "vm1", "beta": "vm2"}, plugins)
    farm.cf("up", "alpha", "beta")
    farm.cf("down", "beta")
    assert log.read_text().splitlines() == ["alpha beta", "beta"]

    farm.config({"alpha": "vm2", "beta": "vm2"}, plugins)
    out = farm.cf("up", "alpha", ok=False)
    assert "alpha is pinned to vm1 (e2e pin); not starting it on vm2" in out
    assert running("vm1", "alpha")
    assert not running("vm2", "alpha")
    assert log.read_text().splitlines() == ["alpha beta", "beta"]  # Nothing changed
    farm.config({"alpha": "vm1", "beta": "vm2"}, plugins)
    farm.cf("down", "alpha")


POLICY_STACK = """\
services:
  web:
    image: busybox
    command: sleep infinity
    labels:
      - traefik.enable=true
      - traefik.http.routers.web.rule=Host(`web.${DOMAIN}`)
      - traefik.http.routers.web.entrypoints=wan
"""


def test_traefik_policy(cf: Path) -> None:
    """A router breaking the convention is refused before it starts."""
    farm = Farm("policy", cf)
    write_stack("policed", POLICY_STACK, env="DOMAIN=lab.test\n")
    plugins = {"sync": {}, "traefik-policy": {"entrypoints_require": {"wan": ["websecure"]}}}
    farm.config({"policed": "vm1"}, plugins)
    out = farm.cf("up", "policed", ok=False)
    assert "router web is on entrypoint wan but not on websecure" in out
    assert not running("vm1", "policed")

    write_stack("policed", POLICY_STACK.replace("entrypoints=wan", "entrypoints=websecure,wan"))
    farm.cf("up", "policed")
    assert running("vm1", "policed")
    farm.cf("down", "policed")


DNS_STACK = """\
services:
  web:
    image: busybox
    command: sleep infinity
    labels:
      - traefik.enable=true
      - traefik.http.routers.{name}.rule=Host(`{name}.${{DOMAIN}}`)
"""


def test_traefik_dns(cf: Path) -> None:
    """Records follow the Traefik hostnames; the reader restarts only when they change."""
    farm = Farm("dns", cf)
    write_stack("dnsreader", "services:\n  app:\n    image: busybox\n    command: sleep infinity\n")
    (STACKS / "dnsreader" / "records.yaml").write_text(
        "extra_records:\n  # BEGIN E2E DNS\n  # END E2E DNS\n"
    )
    write_stack("grafana", DNS_STACK.format(name="grafana"), env="DOMAIN=lab.test\n")
    plugins = {
        "sync": {},
        "traefik-dns": {
            "file": "dnsreader/records.yaml",
            "domain": "lab.test",
            "address": "100.64.0.28",
            "marker": "E2E DNS",
            "restart": "dnsreader",
        },
    }

    def started_at() -> str:
        return vm("vm1", "docker inspect -f '{{.State.StartedAt}}' dnsreader-app-1").strip()

    farm.config({"dnsreader": "vm1"}, plugins)
    farm.cf("up", "dnsreader")
    first = started_at()

    farm.config({"dnsreader": "vm1", "grafana": "vm2"}, plugins)
    farm.cf("up", "grafana")
    records = (STACKS / "dnsreader" / "records.yaml").read_text()
    assert "  - name: grafana.lab.test\n    type: A\n    value: 100.64.0.28\n" in records
    # The reader's host got the new file (via sync's before_up) before the restart
    assert vm("vm1", f"cat {STACKS}/dnsreader/records.yaml") == records
    restarted = started_at()
    assert restarted != first

    farm.cf("up", "grafana")  # Same hostnames: no rewrite, no restart
    assert started_at() == restarted

    write_stack("wiki", DNS_STACK.format(name="wiki"), env="DOMAIN=lab.test\n")
    farm.config({"dnsreader": "vm1", "grafana": "vm2", "wiki": "vm3"}, plugins)
    farm.cf("apply")
    names = re.findall(r"- name: (\S+)", (STACKS / "dnsreader" / "records.yaml").read_text())
    assert names == ["grafana.lab.test", "wiki.lab.test"]
    for stack in ("dnsreader", "grafana", "wiki"):
        farm.cf("down", stack)


SYMLINK_STACK = """\
services:
  app:
    image: busybox
    command: sleep infinity
    env_file: [.env]
"""


def test_agenix_symlink(cf: Path) -> None:
    """Symlink mode: services with `env_file: .env` get the decrypted secrets unchanged."""
    token = f"linked-{os.getpid()}"
    _deploy_secrets({"symapp.env": f"API_TOKEN={token}\n"})
    farm = Farm("agenix-symlink", cf)
    write_stack("symapp", SYMLINK_STACK)
    plugins = {"sync": {}, "agenix": {"mode": "symlink", "stacks": {"symapp": "symapp.env"}}}

    farm.config({"symapp": "vm1"}, plugins)
    out = farm.cf("up", "symapp")
    assert token not in out
    assert vm("vm1", f"readlink {STACKS}/symapp/.env").strip() == "/run/agenix/symapp.env"
    env = vm("vm1", "docker exec symapp-app-1 printenv API_TOKEN").strip()
    assert env == token
    farm.cf("down", "symapp")
