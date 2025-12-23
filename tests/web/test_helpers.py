"""Tests for web API helper functions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

if TYPE_CHECKING:
    from compose_farm.config import Config


class TestExtractConfigError:
    """Tests for extract_config_error helper."""

    def test_validation_error_with_location(self) -> None:
        from compose_farm.config import Config, Host
        from compose_farm.web.deps import extract_config_error

        # Trigger a validation error with an extra field
        with pytest.raises(ValidationError) as exc_info:
            Config(
                hosts={"server": Host(address="192.168.1.1")},
                stacks={"app": "server"},
                unknown_field="bad",  # type: ignore[call-arg]
            )

        msg = extract_config_error(exc_info.value)
        assert "unknown_field" in msg
        assert "Extra inputs are not permitted" in msg

    def test_validation_error_nested_location(self) -> None:
        from compose_farm.config import Host
        from compose_farm.web.deps import extract_config_error

        # Trigger a validation error with a nested extra field
        with pytest.raises(ValidationError) as exc_info:
            Host(address="192.168.1.1", bad_key="value")  # type: ignore[call-arg]

        msg = extract_config_error(exc_info.value)
        assert "bad_key" in msg
        assert "Extra inputs are not permitted" in msg

    def test_regular_exception(self) -> None:
        from compose_farm.web.deps import extract_config_error

        exc = ValueError("Something went wrong")
        msg = extract_config_error(exc)
        assert msg == "Something went wrong"

    def test_file_not_found_exception(self) -> None:
        from compose_farm.web.deps import extract_config_error

        exc = FileNotFoundError("Config file not found")
        msg = extract_config_error(exc)
        assert msg == "Config file not found"


class TestValidateYaml:
    """Tests for _validate_yaml helper."""

    def test_valid_yaml(self) -> None:
        from compose_farm.web.routes.api import _validate_yaml

        # Should not raise
        _validate_yaml("key: value")
        _validate_yaml("list:\n  - item1\n  - item2")
        _validate_yaml("")

    def test_invalid_yaml(self) -> None:
        from compose_farm.web.routes.api import _validate_yaml

        with pytest.raises(HTTPException) as exc_info:
            _validate_yaml("key: [unclosed")

        assert exc_info.value.status_code == 400
        assert "Invalid YAML" in exc_info.value.detail


class TestGetStackComposePath:
    """Tests for _get_stack_compose_path helper."""

    def test_stack_found(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _get_stack_compose_path

        path = _get_stack_compose_path("plex")
        assert isinstance(path, Path)
        assert path.name == "compose.yaml"
        assert path.parent.name == "plex"

    def test_stack_not_found(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _get_stack_compose_path

        with pytest.raises(HTTPException) as exc_info:
            _get_stack_compose_path("nonexistent")

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail


class TestRenderContainers:
    """Tests for container template rendering."""

    def test_render_running_container(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "running"}]
        html = _render_containers("plex", "server-1", containers)

        assert "badge-success" in html
        assert "plex" in html
        assert "initExecTerminal" in html

    def test_render_unknown_state(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "unknown"}]
        html = _render_containers("plex", "server-1", containers)

        assert "loading-spinner" in html

    def test_render_exited_success(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "exited", "ExitCode": 0}]
        html = _render_containers("plex", "server-1", containers)

        assert "badge-neutral" in html
        assert "exited (0)" in html

    def test_render_exited_error(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "exited", "ExitCode": 1}]
        html = _render_containers("plex", "server-1", containers)

        assert "badge-error" in html
        assert "exited (1)" in html

    def test_render_other_state(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "restarting"}]
        html = _render_containers("plex", "server-1", containers)

        assert "badge-warning" in html
        assert "restarting" in html

    def test_render_with_header(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [{"Name": "plex", "State": "running"}]
        html = _render_containers("plex", "server-1", containers, show_header=True)

        assert "server-1" in html
        assert "font-semibold" in html

    def test_render_multiple_containers(self, mock_config: Config) -> None:
        from compose_farm.web.routes.api import _render_containers

        containers = [
            {"Name": "app-web-1", "State": "running"},
            {"Name": "app-db-1", "State": "running"},
        ]
        html = _render_containers("app", "server-1", containers)

        assert "app-web-1" in html
        assert "app-db-1" in html
