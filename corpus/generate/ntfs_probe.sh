#!/usr/bin/env bash
# Granular probe: at which stage does MFT-resident content actually die?
# Snapshots the volume after each step so we can locate the mechanism instead
# of guessing at it. Files only; refuses block devices.
set -eu

OUT="${1:?usage: ntfs_probe.sh <output-dir>}"
[ -b "$OUT" ] && { echo "REFUSING: block device" >&2; exit 1; }
mkdir -p "$OUT"
MNT=$(mktemp -d)
IMG="$OUT/probe.img"

rm -f "$IMG"; dd if=/dev/zero of="$IMG" bs=1M count=64 status=none
mkntfs -Q -F -L PROBE "$IMG" >/dev/null 2>&1
LOOP=$(losetup --find --show "$IMG")
trap 'umount "$MNT" 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true; rmdir "$MNT" 2>/dev/null||true' EXIT

snap () { sync; umount "$MNT" 2>/dev/null || true; cp "$IMG" "$OUT/$1"; mount -t ntfs-3g "$LOOP" "$MNT"; }

mount -t ntfs-3g "$LOOP" "$MNT"
printf 'CANARY-ALPHA-7719 handler DELTA-9 account 4471-9920-3318\n' > "$MNT/case-note.txt"
head -c 8000 /dev/urandom > "$MNT/decoy.bin"
snap "s1_created.img"

rm -f "$MNT/case-note.txt"
snap "s2_deleted.img"

# a single small new file -- can it reclaim the freed MFT record?
printf 'unrelated\n' > "$MNT/newfile.txt"
snap "s3_one_new_file.img"

# full free-space wipe
dd if=/dev/zero of="$MNT/.wipe" bs=1M status=none 2>/dev/null || true
sync; rm -f "$MNT/.wipe"
snap "s4_freespace_wiped.img"

umount "$MNT" 2>/dev/null || true; losetup -d "$LOOP"; trap - EXIT; rmdir "$MNT" 2>/dev/null || true
echo "snapshots written to $OUT"
