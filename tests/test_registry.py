"""Tests for registry module."""

from compose_farm.registry import (
    DOCKER_HUB_ALIASES,
    ImageRef,
    RegistryClient,
    TagCheckResult,
    _find_updates,
    _parse_version,
)


class TestImageRef:
    """Tests for ImageRef parsing."""

    def test_parse_simple_image(self) -> None:
        """Test parsing simple image name."""
        ref = ImageRef.parse("nginx")
        assert ref.registry == "docker.io"
        assert ref.namespace == "library"
        assert ref.name == "nginx"
        assert ref.tag == "latest"

    def test_parse_image_with_tag(self) -> None:
        """Test parsing image with tag."""
        ref = ImageRef.parse("nginx:1.25")
        assert ref.registry == "docker.io"
        assert ref.namespace == "library"
        assert ref.name == "nginx"
        assert ref.tag == "1.25"

    def test_parse_image_with_namespace(self) -> None:
        """Test parsing image with namespace."""
        ref = ImageRef.parse("linuxserver/jellyfin:latest")
        assert ref.registry == "docker.io"
        assert ref.namespace == "linuxserver"
        assert ref.name == "jellyfin"
        assert ref.tag == "latest"

    def test_parse_ghcr_image(self) -> None:
        """Test parsing GitHub Container Registry image."""
        ref = ImageRef.parse("ghcr.io/user/repo:v1.0.0")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "user"
        assert ref.name == "repo"
        assert ref.tag == "v1.0.0"

    def test_parse_image_with_digest(self) -> None:
        """Test parsing image with digest."""
        ref = ImageRef.parse("nginx:latest@sha256:abc123")
        assert ref.registry == "docker.io"
        assert ref.name == "nginx"
        assert ref.tag == "latest"
        assert ref.digest == "sha256:abc123"

    def test_full_name_with_namespace(self) -> None:
        """Test full_name property with namespace."""
        ref = ImageRef.parse("linuxserver/jellyfin")
        assert ref.full_name == "linuxserver/jellyfin"

    def test_full_name_without_namespace(self) -> None:
        """Test full_name property for official images."""
        ref = ImageRef.parse("nginx")
        assert ref.full_name == "library/nginx"

    def test_display_name_official_image(self) -> None:
        """Test display_name for official Docker Hub images."""
        ref = ImageRef.parse("nginx:latest")
        assert ref.display_name == "nginx"

    def test_display_name_hub_with_namespace(self) -> None:
        """Test display_name for Docker Hub images with namespace."""
        ref = ImageRef.parse("linuxserver/jellyfin")
        assert ref.display_name == "linuxserver/jellyfin"

    def test_display_name_other_registry(self) -> None:
        """Test display_name for other registries."""
        ref = ImageRef.parse("ghcr.io/user/repo")
        assert ref.display_name == "ghcr.io/user/repo"


class TestParseVersion:
    """Tests for version parsing."""

    def test_parse_semver(self) -> None:
        """Test parsing semantic version."""
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_parse_version_with_v_prefix(self) -> None:
        """Test parsing version with v prefix."""
        assert _parse_version("v1.2.3") == (1, 2, 3)
        assert _parse_version("V1.2.3") == (1, 2, 3)

    def test_parse_two_part_version(self) -> None:
        """Test parsing two-part version."""
        assert _parse_version("1.25") == (1, 25)

    def test_parse_single_number(self) -> None:
        """Test parsing single number version."""
        assert _parse_version("7") == (7,)

    def test_parse_invalid_version(self) -> None:
        """Test parsing non-version tags."""
        assert _parse_version("latest") is None
        assert _parse_version("stable") is None
        assert _parse_version("alpine") is None


class TestFindUpdates:
    """Tests for finding available updates."""

    def test_find_updates_with_newer_versions(self) -> None:
        """Test finding newer versions."""
        current = "1.0.0"
        tags = ["0.9.0", "1.0.0", "1.1.0", "2.0.0"]
        updates = _find_updates(current, tags)
        assert updates == ["2.0.0", "1.1.0"]

    def test_find_updates_no_newer(self) -> None:
        """Test when already on latest."""
        current = "2.0.0"
        tags = ["1.0.0", "1.5.0", "2.0.0"]
        updates = _find_updates(current, tags)
        assert updates == []

    def test_find_updates_non_version_tag(self) -> None:
        """Test with non-version current tag."""
        current = "latest"
        tags = ["1.0.0", "2.0.0"]
        updates = _find_updates(current, tags)
        # Can't determine updates for non-version tags
        assert updates == []


class TestRegistryClient:
    """Tests for unified registry client."""

    def test_docker_hub_normalization(self) -> None:
        """Test Docker Hub aliases are normalized."""
        for alias in DOCKER_HUB_ALIASES:
            client = RegistryClient(alias)
            assert client.registry == "docker.io"
            assert client.registry_url == "https://registry-1.docker.io"

    def test_ghcr_client(self) -> None:
        """Test GitHub Container Registry client."""
        client = RegistryClient("ghcr.io")
        assert client.registry == "ghcr.io"
        assert client.registry_url == "https://ghcr.io"

    def test_generic_registry(self) -> None:
        """Test generic registry client."""
        client = RegistryClient("quay.io")
        assert client.registry == "quay.io"
        assert client.registry_url == "https://quay.io"


class TestTagCheckResult:
    """Tests for TagCheckResult."""

    def test_create_result(self) -> None:
        """Test creating a result."""
        ref = ImageRef.parse("nginx:1.25")
        result = TagCheckResult(
            image=ref,
            current_digest="sha256:abc",
            available_updates=["1.26", "1.27"],
        )
        assert result.image.name == "nginx"
        assert result.available_updates == ["1.26", "1.27"]
        assert result.error is None

    def test_result_with_error(self) -> None:
        """Test result with error."""
        ref = ImageRef.parse("nginx")
        result = TagCheckResult(
            image=ref,
            current_digest="",
            error="Connection refused",
        )
        assert result.error == "Connection refused"
        assert result.available_updates == []
