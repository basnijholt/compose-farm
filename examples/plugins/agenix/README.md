# compose-farm-agenix

Example [compose-farm plugin](../../../docs/plugins.md) for secrets that [agenix](https://github.com/ryantm/agenix) (or sops-nix) decrypts on each host, instead of plaintext `.env` files in your stacks repo.

It does two things for the stacks you list:

- **`compose_args`**: passes each secret env file to `docker compose` as `--env-file`, so its variables work in `${VAR}` interpolation in `compose.yaml`. The stack's own `.env` is passed first when it exists, because any `--env-file` stops compose from reading `.env` on its own.
- **`preflight`**: checks with one SSH call that every secret is readable on the target host. A migration to a host that lacks a secret fails before the old host is stopped.

Stacks that are not listed are left alone.

> If containers only need the variables (no `${VAR}` in `compose.yaml`), `env_file: /run/agenix/mealie.env` in the service works without this plugin. You still get the preflight check by listing the file.

## Install

```yaml
# compose-farm.yaml
plugin_packages:
  - github:basnijholt/compose-farm/examples/plugins/agenix
```

Then run `cf plugins install`. The Docker image already includes this plugin.

## Configure

```yaml
# compose-farm.yaml
plugins:
  agenix:
    secrets_dir: /run/agenix          # default
    stacks:
      mealie: mealie.env              # one env file
      forgejo: [forgejo.env, db.env]  # several env files
      vaultwarden:
        env: vaultwarden.env
        files: [vaultwarden-admin-token]  # only checked; mounted via compose `secrets:`
```

Relative names are resolved in `secrets_dir`; absolute paths are used as-is.

File secrets listed under `files` pair with compose's own `secrets:`, which mounts them at `/run/secrets/<name>` inside the container:

```yaml
# /opt/stacks/vaultwarden/compose.yaml
services:
  vaultwarden:
    image: vaultwarden/server
    env_file: [.env]
    environment:
      ADMIN_TOKEN_FILE: /run/secrets/admin-token
    secrets: [admin-token]
secrets:
  admin-token:
    file: /run/agenix/vaultwarden-admin-token
```

## Symlink mode

```yaml
plugins:
  agenix:
    mode: symlink
    stacks:
      mealie: mealie.env
```

Before each start, `.env` in the stack directory on the target host becomes a symlink to the decrypted file (`.env -> /run/agenix/mealie.env`). Compose then reads the secrets both for `${VAR}` interpolation and for services with `env_file: .env`, so existing compose files work unchanged. With the default `--env-file` mode, `env_file: .env` in a service still loads the plain `.env`, because the flag only changes interpolation.

- Each listed stack needs exactly one env file, which replaces `.env`. Put non-secret settings such as `DOMAIN` in it too.
- A regular `.env` file is never overwritten: the start fails until you move it.
- compose-farm also reads `.env` on the machine running `cf` (for Traefik labels and ports), so that machine needs the same `/run/agenix` file. Otherwise it sees a broken symlink and variables like `DOMAIN` are missing there.
- With a shared (NFS) stack directory there is one symlink, and each host resolves it to its own `/run/agenix`.

## Keeping secrets off disk

In both modes the decrypted file stays on the host's `/run` ramfs; nothing copies it. ⚠️ Secrets that reach a container as environment variables are still written to disk, because Docker stores each container's environment in `/var/lib/docker/containers/<id>/config.v2.json` (and shows it in `docker inspect`). To keep a secret off disk entirely, mount it as a file through compose `secrets:` and point the app at it, usually with a `*_FILE` variable (see the vaultwarden example above). List the file under `files:` so preflight checks it.

## NixOS side

Decrypt the secrets on **every host that may run the stack**; a stack can only migrate to hosts that have its secrets.

```nix
# secrets/secrets.nix: encrypt for every host that may run the stack
let
  nas = "ssh-ed25519 AAAA...";
  nuc = "ssh-ed25519 AAAA...";
in {
  "mealie.env.age".publicKeys = [ nas nuc ];
}
```

```nix
# common module imported by those hosts
age.secrets."mealie.env" = {
  file = ../secrets/mealie.env.age;
  owner = "bas";  # the user compose-farm connects as: docker compose reads the file
  mode = "0400";
};
# decrypted to /run/agenix/mealie.env
```

## Notes

- Keep non-secret settings (`DOMAIN`, ports, image tags) in the stack's `.env`. compose-farm parses `.env` locally for Traefik labels and port checks; it cannot see variables that only exist in `/run/agenix`.
- The `.env` check happens on the machine running `cf`. That works because compose-farm already needs `compose_dir` there (NFS or the `sync` plugin).
- Never put secret values in `compose_args`. The paths it adds are printed with every compose command.
