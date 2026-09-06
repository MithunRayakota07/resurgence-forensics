#!/usr/bin/env bash
#
# Realistic residue scenario, measured rather than asserted.
#
# A single deleted file is a bad test: its MFT record is the obvious first-fit
# candidate and the very next file created reclaims it (measured -- see
# ntfs_probe.sh). A real volume carries thousands of free MFT records, so a
# wiper that creates a handful of files reclaims a handful.
#
# So this builds a volume with MANY small resident files, deletes most of them,
# and then runs the free-space wipe that tools advertise as making deleted
# files unrecoverable. The question is not "does one file survive" but "what
# FRACTION of deleted resident content is still readable afterwards".
#
# Files only. Refuses block devices.
set -eu

OUT="${1:?usage: ntfs_residue.sh <output-dir>}"
N="${2:-400}"
SIZE_MB="${3:-256}"
[ -b "$OUT" ] && { echo "REFUSING: block device" >&2; exit 1; }
mkdir -p "$OUT"
MNT=$(mktemp -d)
IMG="$OUT/work.img"

rm -f "$IMG"; dd if=/dev/zero of="$IMG" bs=1M count="$SIZE_MB" status=none
mkntfs -Q -F -L SUTRA_RESIDUE "$IMG" >/dev/null 2>&1
LOOP=$(losetup --find --show "$IMG")
trap 'umount "$MNT" 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true; rmdir "$MNT" 2>/dev/null||true' EXIT
mount -t ntfs-3g "$LOOP" "$MNT"

echo "== creating $N small resident case files"
mkdir -p "$MNT/cases"
for i in $(seq 1 "$N"); do
  printf 'CASE-RECORD %04d\nCANARY-%04d-7719\nsubject transferred funds; handler DELTA-%d\naccount 4471-9920-%04d\n' \
    "$i" "$i" $((i % 9)) "$i" > "$MNT/cases/case-$i.txt"
done
# ordinary bulk traffic so the volume is not pathological
for i in $(seq 1 40); do head -c 200000 /dev/urandom > "$MNT/bulk-$i.bin"; done
sync; umount "$MNT"; cp "$IMG" "$OUT/v1_original.img"; mount -t ntfs-3g "$LOOP" "$MNT"
echo "== v1_original.img"

echo "== deleting 90% of the case files (ordinary deletion)"
for i in $(seq 1 $((N * 9 / 10))); do rm -f "$MNT/cases/case-$i.txt"; done
sync; umount "$MNT"; cp "$IMG" "$OUT/v2_deleted.img"; mount -t ntfs-3g "$LOOP" "$MNT"
echo "== v2_deleted.img"

echo "== free-space wipe (what conventional tools do)"
dd if=/dev/zero of="$MNT/.wipe" bs=1M status=none 2>/dev/null || true
sync; rm -f "$MNT/.wipe"; sync
umount "$MNT"; cp "$IMG" "$OUT/v3_freespace_wiped.img"
echo "== v3_freespace_wiped.img"

losetup -d "$LOOP"; trap - EXIT; rmdir "$MNT" 2>/dev/null || true
ls -la "$OUT"/*.img
