"""
The safety interlocks on the destructive side.

This is the part of the repo that can destroy someone's data. The project rule
is "loopback images only, never a physical device, dry-run by default", and it
is supposed to be enforced in code rather than by convention -- so these tests
exist to keep it that way. If any of them ever fail, the tool is not safe to
publish, never mind ship.
"""

from __future__ import annotations

import os
import stat

import pytest

from erase.drive import assert_safe_target_regular, sanitize
from erase.wipe_residue import RefusedUnsafe, assert_safe_target

GUARDS = [assert_safe_target, assert_safe_target_regular]
GUARD_IDS = ["ntfs-guard", "regular-file-guard"]


def _fake_stat(mode_bits: int):
    """A stat_result whose file-type bits we control."""
    return os.stat_result((mode_bits, 0, 0, 1, 0, 0, 0, 0, 0, 0))


# --------------------------------------------------------------------------
# both guards refuse the same categories of target
# --------------------------------------------------------------------------

@pytest.mark.parametrize("guard", GUARDS, ids=GUARD_IDS)
def test_refuses_a_path_that_does_not_exist(guard, tmp_path):
    with pytest.raises(RefusedUnsafe, match="does not exist"):
        guard(str(tmp_path / "nope.img"))


@pytest.mark.parametrize("guard", GUARDS, ids=GUARD_IDS)
def test_refuses_a_directory(guard, tmp_path):
    d = tmp_path / "a_directory"
    d.mkdir()
    with pytest.raises(RefusedUnsafe):
        guard(str(d))


@pytest.mark.parametrize("guard", GUARDS, ids=GUARD_IDS)
def test_refuses_a_block_device(guard, tmp_path, monkeypatch):
    """
    The interlock that matters most: never write to a physical disk.

    Block devices cannot be created in a temp directory (and do not exist at
    all on Windows), so the file-type bits are faked. That still exercises the
    real branch -- the guard's decision is made purely from st_mode.
    """
    target = tmp_path / "looks_like_a_disk"
    target.write_bytes(b"\x00" * 512)
    monkeypatch.setattr(os, "stat", lambda *a, **k: _fake_stat(stat.S_IFBLK | 0o660))
    with pytest.raises(RefusedUnsafe, match="BLOCK DEVICE"):
        guard(str(target))


@pytest.mark.parametrize("guard", GUARDS, ids=GUARD_IDS)
def test_refuses_a_character_device(guard, tmp_path, monkeypatch):
    target = tmp_path / "looks_like_a_tty"
    target.write_bytes(b"\x00" * 512)
    monkeypatch.setattr(os, "stat", lambda *a, **k: _fake_stat(stat.S_IFCHR | 0o660))
    with pytest.raises(RefusedUnsafe, match="character device"):
        guard(str(target))


# --------------------------------------------------------------------------
# where the two guards differ
# --------------------------------------------------------------------------

def test_residue_guard_refuses_a_non_ntfs_file(tmp_path):
    """Module 2 parses MFT records, so it must refuse anything it cannot read
    as NTFS rather than write at guessed offsets."""
    f = tmp_path / "not_ntfs.img"
    f.write_bytes(b"\x00" * (1 << 20))
    with pytest.raises(RefusedUnsafe, match="not an NTFS volume"):
        assert_safe_target(str(f))


def test_drive_guard_accepts_a_plain_image_file(tmp_path):
    """Module 1 overwrites whole images, so it must NOT require NTFS."""
    f = tmp_path / "plain.img"
    f.write_bytes(b"\xab" * (1 << 20))
    assert_safe_target_regular(str(f))          # must not raise


# --------------------------------------------------------------------------
# dry run must be genuinely dry
# --------------------------------------------------------------------------

def test_dry_run_does_not_modify_the_image(tmp_path):
    f = tmp_path / "evidence.img"
    original = bytes(range(256)) * 4096
    f.write_bytes(original)

    result = sanitize(str(f), "image", apply=False, i_understand=False)

    assert result["applied"] is False
    assert f.read_bytes() == original, "a dry run wrote to the image"


def test_apply_without_i_understand_does_not_wipe(tmp_path):
    """--apply alone is not enough; the second interlock must hold."""
    f = tmp_path / "evidence.img"
    original = bytes(range(256)) * 4096
    f.write_bytes(original)

    result = sanitize(str(f), "image", apply=True, i_understand=False)

    assert result["applied"] is False
    assert f.read_bytes() == original, "a single flag was enough to destroy data"


def test_unapplied_run_never_reads_as_an_approved_wipe(tmp_path):
    """
    A plan is not a wipe. If nothing was executed the verdict must not come
    back APPROVE, or a dry run could be presented as a completed sanitization.
    """
    f = tmp_path / "evidence.img"
    f.write_bytes(b"\xcd" * (1 << 20))

    result = sanitize(str(f), "image", apply=False, i_understand=False)

    assert result["applied"] is False
    assert result["validation_verdict"] != "APPROVE"


# --------------------------------------------------------------------------
# and the destructive path, when it is actually asked for
# --------------------------------------------------------------------------

def test_both_flags_together_do_wipe_the_image(tmp_path):
    """The interlocks must not be so tight that the tool cannot do its job."""
    f = tmp_path / "evidence.img"
    f.write_bytes(b"\xab" * (1 << 20))

    result = sanitize(str(f), "image", apply=True, i_understand=True)

    assert result["applied"] is True
    assert set(f.read_bytes()) == {0}, "overwrite did not zero every byte"
    assert result["sha_before"] != result["sha_after"]
