"""Container registry API clients for tag discovery."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

# Image reference pattern: [registry/][namespace/]name[:tag][@digest]
IMAGE_PATTERN = re.compile(
    r"^(?:(?P<registry>[^/]+\.[^/]+)/)?(?:(?P<namespace>[^/:@]+)/)?(?P<name>[^/:@]+)(?::(?P<tag>[^@]+))?(?:@(?P<digest>.+))?$"
)

# Common Docker Hub aliases
DOCKER_HUB_ALIASES = frozenset(
    {"docker.io", "index.docker.io", "registry.hub.docker.com", "registry-1.docker.io"}
)

# HTTP status code
HTTP_OK = 200


@dataclass(frozen=True)
class ImageRef:
    """Parsed container image reference."""

    registry: str  # e.g., "docker.io", "ghcr.io"
    namespace: str  # e.g., "library", "linuxserver"
    name: str  # e.g., "nginx", "jellyfin"
    tag: str  # e.g., "latest", "1.25.0"
    digest: str | None = None  # e.g., "sha256:abc..."

    @property
    def full_name(self) -> str:
        """Full image name with namespace."""
        if self.namespace:
            return f"{self.namespace}/{self.name}"
        return self.name

    @property
    def display_name(self) -> str:
        """Display name (omits docker.io/library for official images)."""
        if self.registry in DOCKER_HUB_ALIASES and self.namespace == "library":
            return self.name
        if self.registry in DOCKER_HUB_ALIASES:
            return self.full_name
        return f"{self.registry}/{self.full_name}"

    @classmethod
    def parse(cls, image: str) -> ImageRef:
        """Parse an image string into components.

        Handles various formats:
        - nginx:latest -> docker.io/library/nginx:latest
        - linuxserver/jellyfin:latest -> docker.io/linuxserver/jellyfin:latest
        - ghcr.io/user/repo:tag -> ghcr.io/user/repo:tag
        """
        match = IMAGE_PATTERN.match(image)
        if not match:
            # Fallback for unparseable images
            return cls(
                registry="docker.io",
                namespace="library",
                name=image.split(":")[0].split("@")[0],
                tag="latest",
            )

        groups = match.groupdict()
        registry = groups.get("registry") or "docker.io"
        namespace = groups.get("namespace") or ""
        name = groups.get("name") or image
        tag = groups.get("tag") or "latest"
        digest = groups.get("digest")

        # Docker Hub official images have implicit "library" namespace
        if registry in DOCKER_HUB_ALIASES and not namespace:
            namespace = "library"

        return cls(
            registry=registry,
            namespace=namespace,
            name=name,
            tag=tag,
            digest=digest,
        )


@dataclass(frozen=True)
class TagInfo:
    """Information about a single tag."""

    name: str
    digest: str | None = None


@dataclass
class TagCheckResult:
    """Result of checking tags for an image."""

    image: ImageRef
    current_digest: str
    equivalent_tags: list[str] = field(default_factory=list)
    available_updates: list[str] = field(default_factory=list)
    all_tags: list[TagInfo] = field(default_factory=list)
    error: str | None = None


class RegistryClient(ABC):
    """Abstract base for registry API clients."""

    @abstractmethod
    async def get_tags(self, image: ImageRef, client: httpx.AsyncClient) -> list[TagInfo]:
        """Fetch available tags for an image."""

    @abstractmethod
    async def get_digest(self, image: ImageRef, tag: str, client: httpx.AsyncClient) -> str | None:
        """Get digest for a specific tag."""


class DockerHubClient(RegistryClient):
    """Docker Hub registry client."""

    AUTH_URL = "https://auth.docker.io/token"
    REGISTRY_URL = "https://registry-1.docker.io"

    async def _get_token(self, image: ImageRef, client: httpx.AsyncClient) -> str | None:
        """Get anonymous auth token for Docker Hub."""
        scope = f"repository:{image.full_name}:pull"
        resp = await client.get(
            self.AUTH_URL,
            params={"service": "registry.docker.io", "scope": scope},
        )
        if resp.status_code == HTTP_OK:
            token: str | None = resp.json().get("token")
            return token
        return None

    async def get_tags(self, image: ImageRef, client: httpx.AsyncClient) -> list[TagInfo]:
        """Fetch available tags from Docker Hub."""
        token = await self._get_token(image, client)
        if not token:
            return []

        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.REGISTRY_URL}/v2/{image.full_name}/tags/list"
        resp = await client.get(url, headers=headers)

        if resp.status_code != HTTP_OK:
            return []

        data = resp.json()
        tag_names = data.get("tags", [])

        # For now, return tags without digests (fetching each digest is slow)
        return [TagInfo(name=name) for name in tag_names]

    async def get_digest(self, image: ImageRef, tag: str, client: httpx.AsyncClient) -> str | None:
        """Get digest for a specific tag."""
        token = await self._get_token(image, client)
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.docker.distribution.manifest.v2+json, "
            "application/vnd.oci.image.manifest.v1+json, "
            "application/vnd.oci.image.index.v1+json",
        }
        url = f"{self.REGISTRY_URL}/v2/{image.full_name}/manifests/{tag}"
        resp = await client.head(url, headers=headers)

        if resp.status_code == HTTP_OK:
            digest: str | None = resp.headers.get("docker-content-digest")
            return digest
        return None


class GhcrClient(RegistryClient):
    """GitHub Container Registry client."""

    TOKEN_URL = "https://ghcr.io/token"  # noqa: S105
    REGISTRY_URL = "https://ghcr.io"

    async def _get_token(self, image: ImageRef, client: httpx.AsyncClient) -> str | None:
        """Get anonymous token for ghcr.io."""
        scope = f"repository:{image.full_name}:pull"
        resp = await client.get(self.TOKEN_URL, params={"scope": scope})
        if resp.status_code == HTTP_OK:
            token: str | None = resp.json().get("token")
            return token
        return None

    async def get_tags(self, image: ImageRef, client: httpx.AsyncClient) -> list[TagInfo]:
        """Fetch available tags from ghcr.io."""
        token = await self._get_token(image, client)
        if not token:
            return []

        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.REGISTRY_URL}/v2/{image.full_name}/tags/list"
        resp = await client.get(url, headers=headers)

        if resp.status_code != HTTP_OK:
            return []

        data = resp.json()
        return [TagInfo(name=name) for name in data.get("tags", [])]

    async def get_digest(self, image: ImageRef, tag: str, client: httpx.AsyncClient) -> str | None:
        """Get digest for a specific tag."""
        token = await self._get_token(image, client)
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, "
            "application/vnd.oci.image.manifest.v1+json",
        }
        url = f"{self.REGISTRY_URL}/v2/{image.full_name}/manifests/{tag}"
        resp = await client.head(url, headers=headers)

        if resp.status_code == HTTP_OK:
            digest: str | None = resp.headers.get("docker-content-digest")
            return digest
        return None


class GenericOciClient(RegistryClient):
    """Generic OCI Distribution API client for other registries."""

    def __init__(self, registry: str) -> None:
        """Initialize with registry hostname."""
        self.registry_url = f"https://{registry}"

    async def get_tags(self, image: ImageRef, client: httpx.AsyncClient) -> list[TagInfo]:
        """Fetch available tags using OCI Distribution API."""
        url = f"{self.registry_url}/v2/{image.full_name}/tags/list"
        resp = await client.get(url)

        if resp.status_code != HTTP_OK:
            return []

        data = resp.json()
        return [TagInfo(name=name) for name in data.get("tags", [])]

    async def get_digest(self, image: ImageRef, tag: str, client: httpx.AsyncClient) -> str | None:
        """Get digest for a specific tag."""
        headers = {
            "Accept": "application/vnd.oci.image.index.v1+json, "
            "application/vnd.oci.image.manifest.v1+json, "
            "application/vnd.docker.distribution.manifest.v2+json"
        }
        url = f"{self.registry_url}/v2/{image.full_name}/manifests/{tag}"
        resp = await client.head(url, headers=headers)

        if resp.status_code == HTTP_OK:
            digest: str | None = resp.headers.get("docker-content-digest")
            return digest
        return None


def get_registry_client(image: ImageRef) -> RegistryClient:
    """Get appropriate client for an image's registry."""
    registry = image.registry.lower()
    if registry in DOCKER_HUB_ALIASES:
        return DockerHubClient()
    if registry == "ghcr.io":
        return GhcrClient()
    return GenericOciClient(registry)


def _parse_version(tag: str) -> tuple[int, ...] | None:
    """Parse a version string into a comparable tuple.

    Returns None if the tag is not a valid version.
    """
    # Strip common prefixes
    tag = tag.lstrip("vV")

    # Try to parse as semver-like (major.minor.patch...)
    parts = tag.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _find_updates(current_tag: str, tags: list[TagInfo], current_digest: str) -> list[str]:
    """Find tags that appear to be newer than current.

    Only considers tags that:
    - Have a different digest than current
    - Parse as a higher version than current
    """
    current_version = _parse_version(current_tag)
    if current_version is None:
        # Can't determine updates for non-version tags like "latest"
        return []

    updates = []
    for tag in tags:
        # Skip if same digest (equivalent tag)
        if tag.digest and tag.digest == current_digest:
            continue

        tag_version = _parse_version(tag.name)
        if tag_version is None:
            continue

        # Compare versions
        if tag_version > current_version:
            updates.append(tag.name)

    # Sort updates in descending order (newest first)
    updates.sort(key=lambda t: _parse_version(t) or (), reverse=True)
    return updates


async def check_image_tags(
    image_str: str,
    current_digest: str,
    client: httpx.AsyncClient,
    fetch_digests: bool = False,
) -> TagCheckResult:
    """Check available tags for an image and compare to current.

    Args:
        image_str: Image string like "nginx:1.25" or "ghcr.io/user/repo:tag"
        current_digest: Digest of the currently running image
        client: httpx async client
        fetch_digests: If True, fetch digest for each tag (slow but accurate)

    Returns:
        TagCheckResult with equivalent tags and available updates

    """
    image = ImageRef.parse(image_str)
    registry_client = get_registry_client(image)

    try:
        # Fetch all tags
        tags = await registry_client.get_tags(image, client)

        equivalent: list[str] = []
        if fetch_digests:
            # Fetch digests for tags to find equivalents (slow)
            for tag in tags:
                digest = await registry_client.get_digest(image, tag.name, client)
                if digest == current_digest:
                    equivalent.append(tag.name)
        else:
            # Just include current tag as equivalent
            equivalent = [image.tag]

        # Find potential updates
        updates = _find_updates(image.tag, tags, current_digest)

        return TagCheckResult(
            image=image,
            current_digest=current_digest,
            equivalent_tags=equivalent,
            available_updates=updates,
            all_tags=tags,
        )
    except Exception as e:
        return TagCheckResult(
            image=image,
            current_digest=current_digest,
            error=str(e),
        )
