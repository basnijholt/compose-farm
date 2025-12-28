"""Container registry API client for tag discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

# Image reference pattern: [registry/][namespace/]name[:tag][@digest]
IMAGE_PATTERN = re.compile(
    r"^(?:(?P<registry>[^/]+\.[^/]+)/)?(?:(?P<namespace>[^/:@]+)/)?(?P<name>[^/:@]+)(?::(?P<tag>[^@]+))?(?:@(?P<digest>.+))?$"
)

# Docker Hub aliases
DOCKER_HUB_ALIASES = frozenset(
    {"docker.io", "index.docker.io", "registry.hub.docker.com", "registry-1.docker.io"}
)

# Token endpoints per registry: (url, extra_params)
TOKEN_ENDPOINTS: dict[str, tuple[str, dict[str, str]]] = {
    "docker.io": ("https://auth.docker.io/token", {"service": "registry.docker.io"}),
    "ghcr.io": ("https://ghcr.io/token", {}),
}

# Registry URL overrides (Docker Hub uses a different host for API)
REGISTRY_URLS: dict[str, str] = {
    "docker.io": "https://registry-1.docker.io",
}

HTTP_OK = 200

MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json"
)


@dataclass(frozen=True)
class ImageRef:
    """Parsed container image reference."""

    registry: str
    namespace: str
    name: str
    tag: str
    digest: str | None = None

    @property
    def full_name(self) -> str:
        """Full image name with namespace."""
        return f"{self.namespace}/{self.name}" if self.namespace else self.name

    @property
    def display_name(self) -> str:
        """Display name (omits docker.io/library for official images)."""
        if self.registry in DOCKER_HUB_ALIASES:
            if self.namespace == "library":
                return self.name
            return self.full_name
        return f"{self.registry}/{self.full_name}"

    @classmethod
    def parse(cls, image: str) -> ImageRef:
        """Parse image string into components."""
        match = IMAGE_PATTERN.match(image)
        if not match:
            return cls("docker.io", "library", image.split(":")[0].split("@")[0], "latest")

        groups = match.groupdict()
        registry = groups.get("registry") or "docker.io"
        namespace = groups.get("namespace") or ""
        name = groups.get("name") or image
        tag = groups.get("tag") or "latest"
        digest = groups.get("digest")

        # Docker Hub official images have implicit "library" namespace
        if registry in DOCKER_HUB_ALIASES and not namespace:
            namespace = "library"

        return cls(registry, namespace, name, tag, digest)


@dataclass
class TagCheckResult:
    """Result of checking tags for an image."""

    image: ImageRef
    current_digest: str
    available_updates: list[str] = field(default_factory=list)
    error: str | None = None


class RegistryClient:
    """Unified OCI Distribution API client."""

    def __init__(self, registry: str) -> None:
        """Initialize for a specific registry."""
        self.registry = registry.lower()
        # Normalize Docker Hub aliases
        if self.registry in DOCKER_HUB_ALIASES:
            self.registry = "docker.io"

        self.registry_url = REGISTRY_URLS.get(self.registry, f"https://{self.registry}")
        self._token_cache: dict[str, str] = {}

    async def _get_token(self, image: ImageRef, client: httpx.AsyncClient) -> str | None:
        """Get auth token for the registry (cached per image)."""
        cache_key = image.full_name
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        endpoint = TOKEN_ENDPOINTS.get(self.registry)
        if not endpoint:
            return None  # No auth needed or unknown registry

        url, extra_params = endpoint
        params = {"scope": f"repository:{image.full_name}:pull", **extra_params}
        resp = await client.get(url, params=params)

        if resp.status_code == HTTP_OK:
            token: str | None = resp.json().get("token")
            if token:
                self._token_cache[cache_key] = token
            return token
        return None

    async def get_tags(self, image: ImageRef, client: httpx.AsyncClient) -> list[str]:
        """Fetch available tags for an image."""
        headers = {}
        token = await self._get_token(image, client)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.registry_url}/v2/{image.full_name}/tags/list"
        resp = await client.get(url, headers=headers)

        if resp.status_code != HTTP_OK:
            return []
        tags: list[str] = resp.json().get("tags", [])
        return tags

    async def get_digest(self, image: ImageRef, tag: str, client: httpx.AsyncClient) -> str | None:
        """Get digest for a specific tag."""
        headers = {"Accept": MANIFEST_ACCEPT}
        token = await self._get_token(image, client)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.registry_url}/v2/{image.full_name}/manifests/{tag}"
        resp = await client.head(url, headers=headers)

        if resp.status_code == HTTP_OK:
            digest: str | None = resp.headers.get("docker-content-digest")
            return digest
        return None


def _parse_version(tag: str) -> tuple[int, ...] | None:
    """Parse version string into comparable tuple."""
    tag = tag.lstrip("vV")
    parts = tag.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _find_updates(current_tag: str, tags: list[str]) -> list[str]:
    """Find tags newer than current based on version comparison."""
    current_version = _parse_version(current_tag)
    if current_version is None:
        return []

    updates = []
    for tag in tags:
        tag_version = _parse_version(tag)
        if tag_version and tag_version > current_version:
            updates.append(tag)

    updates.sort(key=lambda t: _parse_version(t) or (), reverse=True)
    return updates


async def check_image_updates(
    image_str: str,
    client: httpx.AsyncClient,
) -> TagCheckResult:
    """Check if newer versions are available for an image.

    Args:
        image_str: Image string like "nginx:1.25" or "ghcr.io/user/repo:tag"
        client: httpx async client

    Returns:
        TagCheckResult with available updates

    """
    image = ImageRef.parse(image_str)
    registry_client = RegistryClient(image.registry)

    try:
        tags = await registry_client.get_tags(image, client)
        updates = _find_updates(image.tag, tags)
        current_digest = await registry_client.get_digest(image, image.tag, client) or ""

        return TagCheckResult(
            image=image,
            current_digest=current_digest,
            available_updates=updates,
        )
    except Exception as e:
        return TagCheckResult(
            image=image,
            current_digest="",
            error=str(e),
        )
