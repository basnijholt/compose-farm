"""Tests for the example ZFS plugin in examples/plugins/zfs."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.config import Host
from compose_farm.executor import CommandResult
from compose_farm.plugins import HookContext, PluginError
from compose_farm.state import set_stack_host
from tests.plugin_helpers import load_example_plugin, make_config

if TYPE_CHECKING:
    from compose_farm.config import Config

zfs_example = load_example_plugin("zfs")
Snapshot = zfs_example.Snapshot
DS = "tank/data/web"


def snap(name: str, guid: str | None = None, written: int = 0) -> Any:
    """Snapshot whose guid defaults to one derived from its name (same name, same data)."""
    return Snapshot(name, guid or f"guid-{name}", written)


def names(snapshots: list[Any]) -> list[str]:
    return [s.name for s in snapshots]


class FakeZfs:
    """In-memory hosts: host -> dataset -> snapshots (oldest first)."""

    def __init__(
        self,
        hosts: dict[str, dict[str, list[Any]]],
        *,
        unreachable: tuple[str, ...] = (),
        children: dict[tuple[str, str], list[str]] | None = None,
        running: tuple[str, ...] = (),
    ) -> None:
        """Hosts in ``unreachable`` raise like a failed SSH; ``running`` hosts run the project."""
        self.hosts = hosts
        self.unreachable = unreachable
        self.child_datasets = children or {}
        self.running_on = running
        self.calls: list[tuple[Any, ...]] = []
        self.checked: list[str] = []

    def _reach(self, host: str) -> None:
        if host in self.unreachable:
            msg = f"ssh to {host} failed"
            raise PluginError(msg)

    async def exists(self, host: str, name: str) -> bool:
        self._reach(host)
        self.checked.append(host)
        return name in self.hosts[host]

    async def snapshots(self, host: str, name: str) -> list[Any]:
        return list(self.hosts[host][name])

    async def children(self, host: str, name: str) -> list[str]:
        return self.child_datasets.get((host, name), [])

    async def running(self, host: str, stack: str) -> bool:
        self._reach(host)
        return host in self.running_on

    async def snapshot(self, host: str, name: str, snap_name: str) -> None:
        self.calls.append(("snapshot", host))
        self.hosts[host][name].append(snap(snap_name, guid=f"guid-{host}-{snap_name}"))

    async def create(self, host: str, name: str) -> None:
        self.calls.append(("create", host))
        self.hosts[host][name] = []

    async def send(self, src: str, dst: str, name: str, snap_name: str, base: str | None) -> None:
        self.calls.append(("send", src, dst, base))
        source = self.hosts[src][name]
        end = names(source).index(snap_name) + 1
        if base is None:
            self.hosts[dst][name] = [source[end - 1]]
            return
        target = self.hosts[dst][name]
        # recv refuses unless the target's newest snapshot is the base (same guid)
        assert target[-1].name == base
        assert target[-1].guid == source[names(source).index(base)].guid
        self.hosts[dst][name] = target + source[names(source).index(base) + 1 : end]

    async def rename(self, host: str, name: str, new: str) -> None:
        self.calls.append(("rename", host, new))
        self.hosts[host][new] = self.hosts[host].pop(name)

    async def destroy(self, host: str, target: str) -> None:
        self.calls.append(("destroy", host, target))
        if "@" in target:
            name, snap_name = target.split("@")
            self.hosts[host][name] = [s for s in self.hosts[host][name] if s.name != snap_name]
        else:
            del self.hosts[host][target]


def _setup(
    tmp_path: Path,
    fake: FakeZfs,
    options: dict[str, Any] | None = None,
    stacks: dict[str, str | list[str]] | None = None,
) -> tuple[Config, Any]:
    default: dict[str, str | list[str]] = {"web": "h2"}
    cfg = make_config(tmp_path, stacks or default, hosts=("h1", "h2", "h3"))
    plugin = zfs_example.ZfsPlugin({"dataset": "tank/data", **(options or {})})
    plugin.name = "zfs"
    plugin.tool = lambda _ctx: fake
    return cfg, plugin


def _hosts(**datasets: dict[str, list[Any]]) -> dict[str, dict[str, list[Any]]]:
    return {"h1": {}, "h2": {}, "h3": {}, **datasets}


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({}, "dataset is required"),
            ({"dataset": ""}, "dataset is required"),
            ({"dataset": "tank/data", "nope": 1}, "unknown option"),
            ({"dataset": "tank/data", "zfs": ["sudo"]}, "zfs must be a command string"),
            ({"dataset": "tank/data", "stacks": "web"}, "stacks must be a list"),
            ({"dataset": "tank/data", "retire": "hide"}, "retire must be"),
            ({"dataset": "tank/data", "storage_host": 1}, "storage_host must be a host name"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            zfs_example.ZfsPlugin(options)


class TestFirstDeploy:
    """Creating a stack's dataset when it has no previous host."""

    async def test_creates_missing_dataset(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == [("create", "h2")]

    async def test_deployed_stack_skips_the_scan(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h2={DS: [snap("auto-1")]}), unreachable=("h1", "h3"))
        cfg, plugin = _setup(tmp_path, fake)
        set_stack_host(cfg, "web", "h2")
        await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == []
        assert fake.checked == ["h2"]

    async def test_refuses_when_dataset_lives_on_another_host(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h3={DS: [snap("auto-1")]}))
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="already exists on h3"):
            await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == []

    async def test_refuses_stale_target_copy_when_another_exists(self, tmp_path: Path) -> None:
        """After a rolled-back migration and `cf down`, both hosts hold a copy."""
        fake = FakeZfs(_hosts(h1={DS: [snap("a")]}, h2={DS: [snap("a")]}))
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="already exists on h1"):
            await plugin.before_up(HookContext(cfg, "web", "h2"))

    async def test_unreachable_host_blocks_create(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(), unreachable=("h3",))
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="h3"):
            await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == []

    async def test_multi_host_stack_gets_a_dataset_per_host(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: []}))
        cfg, plugin = _setup(tmp_path, fake, stacks={"web": ["h1", "h2"]})
        await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == [("create", "h2")]

    async def test_unmanaged_stack_is_ignored(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake, {"stacks": ["other"]})
        await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert await plugin.preflight(HookContext(cfg, "web", "h2")) == []
        assert fake.calls == []


class TestMigration:
    """Moving the dataset along with the stack."""

    async def test_full_flow(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: [snap("auto-1")]}))
        cfg, plugin = _setup(tmp_path, fake)
        ctx = HookContext(cfg, "web", "h2", "h1")

        await plugin.before_up(ctx)  # Live full send while h1 still runs the stack
        [first] = names(fake.hosts["h2"][DS])
        assert first.startswith("cf-")
        assert fake.calls == [("snapshot", "h1"), ("send", "h1", "h2", None)]

        await plugin.after_source_stopped(ctx)  # Short final incremental
        assert fake.calls[-2:] == [("snapshot", "h1"), ("send", "h1", "h2", first)]
        final = fake.hosts["h1"][DS][-1].name
        assert names(fake.hosts["h2"][DS]) == [first, final]

        await plugin.after_up(ctx)  # Retire the source, keep only the newest cf- snapshot
        assert DS not in fake.hosts["h1"]
        [retired] = fake.hosts["h1"]
        assert retired.startswith(f"{DS}.retired-")
        assert names(fake.hosts["h2"][DS]) == [final]

    async def test_incremental_when_target_has_an_older_copy(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: [snap("auto-1"), snap("auto-2")]}, h2={DS: [snap("auto-1")]}))
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.before_up(HookContext(cfg, "web", "h2", "h1"))
        assert fake.calls[-1] == ("send", "h1", "h2", "auto-1")
        assert names(fake.hosts["h2"][DS])[:2] == ["auto-1", "auto-2"]

    async def test_drops_empty_target_snapshots_taken_after_the_base(self, tmp_path: Path) -> None:
        """Auto-snapshot tools may snapshot the idle received copy; that blocks the receive."""
        fake = FakeZfs(
            _hosts(
                h1={DS: [snap("auto-1"), snap("cf-1")]},
                h2={DS: [snap("cf-1"), snap("autosnap-h2", written=0)]},
            )
        )
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.after_source_stopped(HookContext(cfg, "web", "h2", "h1"))
        assert ("destroy", "h2", f"{DS}@autosnap-h2") in fake.calls
        assert fake.calls[-1] == ("send", "h1", "h2", "cf-1")

    async def test_refuses_target_snapshots_holding_new_data(self, tmp_path: Path) -> None:
        fake = FakeZfs(
            _hosts(h1={DS: [snap("cf-1")]}, h2={DS: [snap("cf-1"), snap("local", written=4096)]})
        )
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="changed on h2 after cf-1"):
            await plugin.after_source_stopped(HookContext(cfg, "web", "h2", "h1"))
        assert not any(call[0] in ("destroy", "send") for call in fake.calls)

    async def test_same_name_different_data_is_not_a_common_base(self, tmp_path: Path) -> None:
        """Independent sanoid snapshots share names; only the guid proves shared ancestry."""
        fake = FakeZfs(
            _hosts(
                h1={DS: [snap("autosnap_19:30", guid="g1")]},
                h2={DS: [snap("autosnap_19:30", guid="g2"), snap("later")]},
            )
        )
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="without a common snapshot"):
            await plugin.before_up(HookContext(cfg, "web", "h2", "h1"))
        assert names(fake.hosts["h2"][DS]) == ["autosnap_19:30", "later"]
        assert not any(call[0] in ("destroy", "send") for call in fake.calls)

    async def test_child_datasets_are_refused(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: []}), children={("h1", DS): [f"{DS}/db"]})
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="child datasets"):
            await plugin.before_up(HookContext(cfg, "web", "h2", "h1"))
        assert fake.calls == []

    async def test_source_not_in_config_is_refused(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="gone, which is no longer in the config"):
            await plugin.before_up(HookContext(cfg, "web", "h2", "gone"))
        assert fake.calls == []

    async def test_unreachable_source_is_refused(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(), unreachable=("h1",))
        cfg, plugin = _setup(tmp_path, fake)
        with pytest.raises(PluginError, match="ssh to h1 failed"):
            await plugin.before_up(HookContext(cfg, "web", "h2", "h1"))
        assert fake.calls == []

    async def test_nothing_to_move_creates_on_target(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.before_up(HookContext(cfg, "web", "h2", "h1"))
        await plugin.after_source_stopped(HookContext(cfg, "web", "h2", "h1"))
        assert fake.calls == [("create", "h2")]


class TestRetire:
    """Removed stacks and migration sources are renamed or destroyed."""

    async def test_removed_stack_is_renamed(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h2={DS: []}))
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        [retired] = fake.hosts["h2"]
        assert retired.startswith(f"{DS}.retired-")

    async def test_destroy_when_opted_in(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h2={DS: []}))
        cfg, plugin = _setup(tmp_path, fake, {"retire": "destroy"})
        await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        assert fake.calls == [("destroy", "h2", DS)]

    async def test_missing_dataset_is_a_no_op(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake)
        await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        assert fake.calls == []


class TestStorageHost:
    """All datasets on one host (a NAS sharing them), so nothing moves."""

    async def test_creates_on_storage_host_whatever_the_target(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        await plugin.before_up(HookContext(cfg, "web", "h2"))
        assert fake.calls == [("create", "h1")]

    async def test_migration_moves_nothing(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: [snap("auto-1")]}))
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        ctx = HookContext(cfg, "web", "h3", "h2")
        await plugin.before_up(ctx)
        await plugin.after_source_stopped(ctx)
        await plugin.after_up(ctx)
        assert fake.calls == []

    async def test_removal_retires_on_storage_host(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: []}))
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        [retired] = fake.hosts["h1"]
        assert retired.startswith(f"{DS}.retired-")

    async def test_removal_waits_until_no_host_runs_the_stack(self, tmp_path: Path) -> None:
        """A multi-host orphan stops host by host; shared data must outlive every instance."""
        fake = FakeZfs(_hosts(h1={DS: []}), running=("h3",))
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        assert fake.calls == []
        fake.running_on = ()
        await plugin.on_stack_removed(HookContext(cfg, "web", "h3"))
        assert fake.calls[0][0] == "rename"

    async def test_removal_fails_when_another_host_is_unreachable(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={DS: []}), unreachable=("h3",))
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        with pytest.raises(PluginError, match="h3"):
            await plugin.on_stack_removed(HookContext(cfg, "web", "h2"))
        assert DS in fake.hosts["h1"]

    async def test_preflight_checks_storage_host(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h1={"tank/data": []}))
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "h1"})
        assert await plugin.preflight(HookContext(cfg, "web", "h2")) == []
        cfg, plugin = _setup(tmp_path, fake, {"storage_host": "nas"})
        [problem] = await plugin.preflight(HookContext(cfg, "web", "h2"))
        assert "storage_host 'nas' is not in the config" in problem


class TestPreflight:
    """The parent dataset must exist on the host."""

    async def test_missing_parent(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts())
        cfg, plugin = _setup(tmp_path, fake)
        [problem] = await plugin.preflight(HookContext(cfg, "web", "h2"))
        assert "tank/data not found" in problem

    async def test_parent_present(self, tmp_path: Path) -> None:
        fake = FakeZfs(_hosts(h2={"tank/data": []}))
        cfg, plugin = _setup(tmp_path, fake)
        assert await plugin.preflight(HookContext(cfg, "web", "h2")) == []


def _result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(
        stack="web", exit_code=exit_code, success=exit_code == 0, stdout=stdout, stderr=stderr
    )


class TestZfsCommands:
    """The real adapter builds the expected shell commands."""

    @staticmethod
    def _ctx(tmp_path: Path) -> HookContext:
        cfg = make_config(tmp_path, {"web": "far"}, hosts=("near", "far"))
        cfg.hosts["far"] = Host(address="192.0.2.10", user="bas")
        return HookContext(cfg, "web", "far", "near")

    async def test_exists(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        with patch.object(HookContext, "run", AsyncMock(return_value=_result(f"{DS}\n"))) as run:
            assert await zfs_example.Zfs(ctx, "sudo zfs").exists("far", DS)
        run.assert_awaited_once_with(
            f"sudo zfs list -H -o name {DS}", host="far", stream=False, check=False
        )

    async def test_missing_dataset_is_absent(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        missing = _result(stderr=f"cannot open '{DS}': dataset does not exist\n", exit_code=1)
        with patch.object(HookContext, "run", AsyncMock(return_value=missing)):
            assert not await zfs_example.Zfs(ctx, "zfs").exists("far", DS)

    async def test_other_errors_are_not_absence(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        denied = _result(stderr="sudo: a password is required\n", exit_code=1)
        with (
            patch.object(HookContext, "run", AsyncMock(return_value=denied)),
            pytest.raises(PluginError, match="a password is required"),
        ):
            await zfs_example.Zfs(ctx, "sudo zfs").exists("far", DS)

    async def test_snapshots_parse_guid_and_written(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        out = f"{DS}@a\t111\t0\n{DS}@b\t222\t4096\n"
        with patch.object(HookContext, "run", AsyncMock(return_value=_result(out))) as run:
            snapshots = await zfs_example.Zfs(ctx, "zfs").snapshots("far", DS)
        assert snapshots == [Snapshot("a", "111", 0), Snapshot("b", "222", 4096)]
        assert run.await_args is not None
        assert "-o name,guid,written" in run.await_args.args[0]

    async def test_running_checks_the_compose_project_label(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        with patch.object(HookContext, "run", AsyncMock(return_value=_result("abc123\n"))) as run:
            assert await zfs_example.Zfs(ctx, "zfs").running("far", "web")
        assert run.await_args is not None
        assert run.await_args.args[0] == (
            "docker ps -q --filter label=com.docker.compose.project=web"
        )

    async def test_send_relays_through_the_cf_machine(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        arrived = _result(f"{DS}@cf-2\t1\t0\n")
        with (
            patch.object(HookContext, "run", AsyncMock(return_value=arrived)),
            patch.object(HookContext, "run_local", AsyncMock()) as run_local,
            patch.object(zfs_example, "get_ssh_auth_sock", return_value=None),
        ):
            await zfs_example.Zfs(ctx, "zfs").send("near", "far", DS, "cf-2", "cf-1")
        assert run_local.await_args is not None
        pipeline = run_local.await_args.args[0]
        send, recv = pipeline.split(" | ")
        assert send == f"zfs send -I @cf-1 {DS}@cf-2"  # near is local: no ssh
        assert recv.startswith("ssh ")
        assert recv.endswith(f"bas@192.0.2.10 'zfs recv -F {DS}'")

    async def test_send_fails_when_snapshot_did_not_arrive(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        with (
            patch.object(HookContext, "run", AsyncMock(return_value=_result(""))),
            patch.object(HookContext, "run_local", AsyncMock()),
            pytest.raises(PluginError, match="did not arrive on far"),
        ):
            await zfs_example.Zfs(ctx, "zfs").send("near", "far", DS, "cf-2", None)


def test_package_registers_entry_point() -> None:
    pyproject = Path(__file__).parent.parent / "examples" / "plugins" / "zfs" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["entry-points"]["compose_farm.plugins"] == {
        "zfs": "compose_farm_zfs:ZfsPlugin"
    }
