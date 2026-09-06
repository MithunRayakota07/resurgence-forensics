#!/usr/bin/env bash
# Emulate what SDelete/KillDisk DOCUMENT doing, so our residue number is
# measured against a correct free-space wipe rather than our own naive one.
#
# SDelete docs: after overwriting free clusters, it "must also fill any
# existing free portions of the NTFS MFT with files that fit within an MFT
# record ... When SDelete can no longer even create a new file, it knows that
# all the previously free records in the MFT have been completely filled".
#
# So: many tiny files until creation fails. That reclaims free MFT records,
# which is the step our dd baseline skipped.
set -eu
IMG="${1:?usage: sdelete_equivalent.sh <image>}"
[ -b "$IMG" ] && { echo "REFUSING block device" >&2; exit 1; }
MNT=$(mktemp -d)
LOOP=$(losetup --find --show "$IMG")
trap 'umount "$MNT" 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true; rmdir "$MNT" 2>/dev/null||true' EXIT
mount -t ntfs-3g "$LOOP" "$MNT"

# 1. fill free clusters (what dd did)
dd if=/dev/zero of="$MNT/.bulk" bs=1M status=none 2>/dev/null || true
sync; rm -f "$MNT/.bulk"; sync

# 2. fill free MFT records with tiny files (the step SDelete adds)
mkdir -p "$MNT/.mftfill"
i=0
while :; do
  if ! printf 'x' > "$MNT/.mftfill/f$i" 2>/dev/null; then break; fi
  i=$((i+1))
  [ $i -ge 100000 ] && break
done
echo "created $i tiny files to fill free MFT records"
sync
rm -rf "$MNT/.mftfill"; sync
umount "$MNT"; losetup -d "$LOOP"; trap - EXIT; rmdir "$MNT" 2>/dev/null || true
