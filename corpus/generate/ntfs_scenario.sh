#!/usr/bin/env bash
#
# Build the residue-recovery scenario on a REAL NTFS filesystem.
#
# Produces three snapshots of the same volume:
#   v1_original.img   evidence file present
#   v2_naive_wipe.img  file deleted, then free space wiped the way a
#                      conventional secure-delete tool does it
#   (v3 is produced by our own eraser, not here)
#
# The claim this exists to test: after v2, a tool that overwrites clusters has
# done everything it knows how to do -- and the file content is still there.
#
# SAFETY. This script only ever touches a regular file passed in as an
# argument, and a loop device bound to it. It refuses to run against a block
# device. Never point it at a disk.

set -euo pipefail

OUT="${1:?usage: ntfs_scenario.sh <output-dir>}"
SIZE_MB="${2:-64}"
MNT=$(mktemp -d)

if [ -b "$OUT" ]; then
  echo "REFUSING: '$OUT' is a block device. This script works on files only." >&2
  exit 1
fi
mkdir -p "$OUT"

IMG="$OUT/work.img"
echo "== creating ${SIZE_MB} MiB image at $IMG"
rm -f "$IMG"
dd if=/dev/zero of="$IMG" bs=1M count="$SIZE_MB" status=none

echo "== formatting NTFS"
mkntfs -Q -F -L SUTRA_DEMO "$IMG" >/dev/null 2>&1

LOOP=$(losetup --find --show "$IMG")
trap 'umount "$MNT" 2>/dev/null || true; losetup -d "$LOOP" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true' EXIT
echo "== loop device: $LOOP"

mount -t ntfs-3g "$LOOP" "$MNT"

# ---- the evidence -----------------------------------------------------------
# Small enough to be stored RESIDENT inside its own MFT record: no data
# cluster is ever allocated for it.
cat > "$MNT/case-note.txt" <<'EOF'
CASE 2026/NTRO/0447 - INTERVIEW NOTE
Subject admitted transferring the files on 14 August.
Account: 4471-9920-3318. Contact: +91-98xxx-xxxxx.
Handler reference DELTA-9. Do not disclose.
EOF

# A larger file for contrast: this one DOES get data clusters, so a
# cluster-overwriting tool can actually reach it.
head -c 200000 /dev/urandom > "$MNT/bulk-evidence.bin"

# Ordinary traffic so the volume is not pathologically empty
for i in 1 2 3 4 5; do
  head -c 8000 /dev/urandom > "$MNT/routine-$i.dat"
done

sync; umount "$MNT"
cp "$IMG" "$OUT/v1_original.img"
echo "== v1_original.img written"

# ---- the naive "secure delete" ---------------------------------------------
mount -t ntfs-3g "$LOOP" "$MNT"

# The realistic sequence, and the one that matters.
#
# The file is deleted NORMALLY -- by a user, or an application, or the Recycle
# Bin. No secure-delete tool was pointed at the file itself while it existed,
# because by the time anyone thinks about sanitising, it is already gone.
#
# (Measured: overwriting a resident file IN PLACE before deleting it does
# destroy the content, because that write lands inside the MFT record. That is
# a different scenario -- a file-targeted wipe -- and it works. It is not the
# scenario that leaves residue, and we do not claim otherwise.)
rm -f "$MNT/case-note.txt"
sync

# ...and then the step that is advertised as making already-deleted files
# unrecoverable: wipe all free space. Filling the volume with a large file
# consumes unallocated CLUSTERS. A resident file never occupied a cluster, and
# its now-unallocated MFT RECORD is not free space by this definition, so the
# wipe cannot reach it.
dd if=/dev/zero of="$MNT/.wipe" bs=1M status=none 2>/dev/null || true
sync
rm -f "$MNT/.wipe"
sync

umount "$MNT"
cp "$IMG" "$OUT/v2_naive_wipe.img"
echo "== v2_naive_wipe.img written"

losetup -d "$LOOP"; trap - EXIT; rmdir "$MNT" 2>/dev/null || true
ls -la "$OUT"/*.img
