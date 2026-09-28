#!/usr/bin/env bash
# Create disposable Incus VMs for compose-farm end-to-end tests.
#
# Everything lives in the Incus project "cf-e2e" and under $E2E_DIR, so
# `e2e/teardown.sh` removes it all. Requires incus (with VM support) and age.
set -euo pipefail

PROJECT=${PROJECT:-cf-e2e}
E2E_DIR=${E2E_DIR:-/tmp/cf-e2e}
VMS=(${VMS:-vm1 vm2 vm3})
IMAGE=${IMAGE:-images:ubuntu/24.04/cloud}

mkdir -p "$E2E_DIR/home/.ssh/compose-farm"
KEY="$E2E_DIR/home/.ssh/compose-farm/id_ed25519"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C cf-e2e -f "$KEY"
PUBKEY=$(cat "$KEY.pub")

if ! incus project show "$PROJECT" >/dev/null 2>&1; then
  incus project create "$PROJECT" \
    -c features.images=false -c features.profiles=false \
    -c features.networks=false -c features.storage.volumes=false
fi

cat > "$E2E_DIR/user-data" <<CLOUD
#cloud-config
package_update: true
packages: [openssh-server, docker.io, docker-compose-v2, zfsutils-linux, rsync, nfs-kernel-server, nfs-common, age]
users:
  - name: cf
    groups: [docker]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys: ["$PUBKEY"]
runcmd:
  - truncate -s 4G /var/lib/tank.img
  - zpool create -m none tank /var/lib/tank.img
  - zfs create -o mountpoint=/mnt/data tank/data
  - zfs create -o mountpoint=/mnt/shared tank/shared
  - mkdir -p /run/agenix
CLOUD

for vm in "${VMS[@]}"; do
  if ! incus info "$vm" --project "$PROJECT" >/dev/null 2>&1; then
    incus launch "$IMAGE" "$vm" --vm --project "$PROJECT" \
      -c limits.cpu=2 -c limits.memory=2GiB -d root,size=12GiB \
      -c cloud-init.user-data="$(cat "$E2E_DIR/user-data")"
  fi
done

ip_of() {  # IPv4 of the VM's own NIC (not docker0)
  incus list "$1" --project "$PROJECT" -f json | jq -r '
    .[0].state.network // {} | to_entries[]
    | select(.key | test("^(enp|eth)")) | .value.addresses[]
    | select(.family == "inet") | .address' | head -n 1
}

for vm in "${VMS[@]}"; do
  until incus exec "$vm" --project "$PROJECT" -- true 2>/dev/null; do sleep 2; done
  incus exec "$vm" --project "$PROJECT" -- cloud-init status --wait >/dev/null || {
    echo "cloud-init failed on $vm" >&2
    incus exec "$vm" --project "$PROJECT" -- cloud-init status --long >&2
    exit 1
  }
  until [ -n "$(ip_of "$vm")" ]; do sleep 1; done
done

: > "$E2E_DIR/home/.ssh/compose-farm/known_hosts"
: > "$E2E_DIR/hosts.env"
for vm in "${VMS[@]}"; do
  ip=$(ip_of "$vm")
  echo "${vm^^}=$ip" >> "$E2E_DIR/hosts.env"
  ssh-keyscan -q -t ed25519 "$ip" >> "$E2E_DIR/home/.ssh/compose-farm/known_hosts"
done

# vm1 is also the "NAS" for storage_host mode: share /mnt/shared, mount it elsewhere
VM1=$(ip_of "${VMS[0]}")
SUBNET="${VM1%.*}.0/24"
incus exec "${VMS[0]}" --project "$PROJECT" -- sh -c \
  "echo '/mnt/shared $SUBNET(rw,crossmnt,no_subtree_check,no_root_squash)' > /etc/exports && exportfs -ra"
for vm in "${VMS[@]:1}"; do
  incus exec "$vm" --project "$PROJECT" -- sh -c \
    "zfs set mountpoint=none tank/shared && mkdir -p /mnt/shared && mountpoint -q /mnt/shared || mount -t nfs4 $VM1:/mnt/shared /mnt/shared"
done

echo "VMs ready:"
cat "$E2E_DIR/hosts.env"
