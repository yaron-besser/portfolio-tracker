"""No test may change a real data file. The guard, not the good intention.

Why this exists. Every writer in `src/` defaults its `path=` to the
production table — `instruments.set_issuer()`, `set_cluster()`,
`set_status()`, `set_note_ref()`, `set_provider_sector()`,
`add_instrument()`, and `ops/refresh_etf_holdings.py`'s `write()` and
`main()` all do. That default is right for a session with Yaron and wrong in
a test, and the difference is one keyword argument that is easy to leave off.

On 2026-10-04 a test did leave it off and wrote to `data/instruments.csv`.
The suite passed. The only thing that caught it was a human noticing a stray
value in the file afterwards.

The fix is not to change the signatures — a defaulted path is what makes the
writers usable in a real session, and a signature change protects only the
stores that exist today. A guard protects every store, including the ones a
later task adds. So: any test that opens a path under `data/` for writing
fails, and the message says what to pass instead.

Reading a real table is fine and is the repo's convention — `tests/` reads
`data/sectors.csv` and `data/clusters.csv` as real reference data.

## What this does NOT cover

Written down because a guard believed to be airtight is more dangerous than
one known to have edges: the first kind gets trusted in places the second
kind would not be. A review on 2026-10-04 got past it by three routes, and
none of them is patched, because nothing in `src/`, `ops/` or `tests/` uses
any of them — adding a patch for a thing nobody calls is cost with no cover.

1. **`io.FileIO`**, the raw file object, which does not go through `open()`.
2. **`_io.open`** — the `_io` extension module's own attribute. `builtins.open`
   and `io.open` are both patched and are the same object, but `_io.open` is a
   third name for it that a replacement of the other two does not reach.
3. **Anything that opens a file in C** without returning through Python's
   `open` at all — `pyarrow.OSFile`, `pyarrow.csv.write_csv`, and any future
   extension that writes its own bytes.

If a module here ever starts using one of these, patch it then, and delete
its line from this list. The shape of the fix is the same as every function
below.

What IS covered, verified by firing real destructive calls at real files in a
throwaway copy of the repository: `open` in every writing mode, `Path.open`,
`Path.write_text`, `Path.write_bytes`, every pandas writer, `os.open`,
`os.remove`, `os.unlink`, `os.truncate`, `os.rmdir`, `os.rename` and
`os.replace` in both directions, `Path.unlink`, `Path.rmdir`,
`shutil.rmtree`, `shutil.move`, `shutil.copyfile`, `shutil.copytree`, mmap,
relative paths, `..` traversal, a symlink to a protected file, and a symlink
whose PARENT directory is the laundered one.
"""
from __future__ import annotations

import builtins
import io
import os
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Only the data tables. `notes/` is prose a test has no reason to touch, and
# pytest's own caches live elsewhere; widening this would start failing tests
# for writing their own temp files.
#
# The directory ITSELF is protected as well as everything under it. The first
# version only had the trailing-separator prefix, so `shutil.rmtree(data/)`
# did not match it — and on 2026-10-04 the test written to prove the guard
# caught deletion deleted `data/` instead. Every tracked file came back from
# git; the gitignored ones did not.
PROTECTED_ROOT = str(PROJECT_ROOT / "data")
PROTECTED = PROTECTED_ROOT + os.sep

# Any of these in a mode string means the file is being opened to be changed.
_WRITE_MODES = frozenset("wxa+")


def _writes(mode) -> bool:
    return any(c in _WRITE_MODES for c in str(mode))


def _refuse_if_protected(target, what: str) -> None:
    """Raise unless `target` is somewhere a test is allowed to change.

    `resolve()`, not `abspath()`. The first version normalised without
    following symlinks, on the grounds that it runs often and `resolve()`
    costs a syscall — and a symlink inside `tmp_path` pointing at a real
    table then wrote straight through it. This is only reached on a WRITE,
    which in a test suite is rare next to reads, so the syscall is affordable
    and a guard with a known hole is worse than none: it gets trusted.
    """
    try:
        path = str(Path(os.fspath(target)).resolve())
    except (TypeError, ValueError, OSError):
        return                       # a file descriptor or a buffer, not a path
    if path != PROTECTED_ROOT and not path.startswith(PROTECTED):
        return
    raise AssertionError(
        f"A test tried to {what} {path}, which is a real data file.\n"
        f"  Writers default their path to the production table, which is\n"
        f"  right in a real session and wrong here. Pass an explicit one:\n"
        f"      path=tmp_path / {Path(path).name!r}\n"
        f"  and build the store it needs in tmp_path first."
    )


@pytest.fixture(scope="session", autouse=True)
def no_writes_to_real_data():
    """Fail any test that opens a file under data/ for writing."""
    patch = pytest.MonkeyPatch()
    real_open, real_path_open = builtins.open, Path.open
    real_write_text, real_write_bytes = Path.write_text, Path.write_bytes
    real_os_open, real_replace, real_rename = os.open, os.replace, os.rename
    # Destroying a table is as bad as corrupting one, and the first version
    # guarded only writes: `Path.unlink()` removed a real file in a probe.
    real_unlink, real_remove, real_os_unlink = Path.unlink, os.remove, os.unlink
    real_truncate, real_rmtree = os.truncate, shutil.rmtree
    real_rmdir, real_os_rmdir = Path.rmdir, os.rmdir

    def guarded_open(file, mode="r", *a, **kw):
        if _writes(mode):
            _refuse_if_protected(file, "write to")
        return real_open(file, mode, *a, **kw)

    def guarded_path_open(self, mode="r", *a, **kw):
        if _writes(mode):
            _refuse_if_protected(self, "write to")
        return real_path_open(self, mode, *a, **kw)

    def guarded_write_text(self, *a, **kw):
        _refuse_if_protected(self, "write to")
        return real_write_text(self, *a, **kw)

    def guarded_write_bytes(self, *a, **kw):
        _refuse_if_protected(self, "write to")
        return real_write_bytes(self, *a, **kw)

    def guarded_os_open(path, flags, *a, **kw):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            _refuse_if_protected(path, "write to")
        return real_os_open(path, flags, *a, **kw)

    def guarded_replace(src, dst, *a, **kw):
        # Both ends: renaming a real table AWAY destroys it exactly as surely
        # as writing over one.
        _refuse_if_protected(src, "move")
        _refuse_if_protected(dst, "replace")
        return real_replace(src, dst, *a, **kw)

    def guarded_rename(src, dst, *a, **kw):
        _refuse_if_protected(src, "move")
        _refuse_if_protected(dst, "rename over")
        return real_rename(src, dst, *a, **kw)

    def guarded_path_unlink(self, *a, **kw):
        _refuse_if_protected(self, "delete")
        return real_unlink(self, *a, **kw)

    def guarded_remove(path, *a, **kw):
        _refuse_if_protected(path, "delete")
        return real_remove(path, *a, **kw)

    def guarded_os_unlink(path, *a, **kw):
        _refuse_if_protected(path, "delete")
        return real_os_unlink(path, *a, **kw)

    def guarded_truncate(path, length, *a, **kw):
        _refuse_if_protected(path, "truncate")
        return real_truncate(path, length, *a, **kw)

    def guarded_rmtree(path, *a, **kw):
        _refuse_if_protected(path, "delete the tree at")
        return real_rmtree(path, *a, **kw)

    def guarded_path_rmdir(self, *a, **kw):
        _refuse_if_protected(self, "remove the directory")
        return real_rmdir(self, *a, **kw)

    def guarded_os_rmdir(path, *a, **kw):
        _refuse_if_protected(path, "remove the directory")
        return real_os_rmdir(path, *a, **kw)

    # `io.open` and `builtins.open` are the same object but are looked up
    # separately, so both names have to be replaced or pandas reaches the
    # original through whichever one this misses.
    patch.setattr(builtins, "open", guarded_open)
    patch.setattr(io, "open", guarded_open)
    patch.setattr(Path, "open", guarded_path_open)
    patch.setattr(Path, "write_text", guarded_write_text)
    patch.setattr(Path, "write_bytes", guarded_write_bytes)
    patch.setattr(os, "open", guarded_os_open)
    patch.setattr(os, "replace", guarded_replace)
    patch.setattr(os, "rename", guarded_rename)
    patch.setattr(Path, "unlink", guarded_path_unlink)
    patch.setattr(Path, "rmdir", guarded_path_rmdir)
    patch.setattr(os, "remove", guarded_remove)
    patch.setattr(os, "unlink", guarded_os_unlink)
    patch.setattr(os, "rmdir", guarded_os_rmdir)
    patch.setattr(os, "truncate", guarded_truncate)
    patch.setattr(shutil, "rmtree", guarded_rmtree)
    try:
        yield
    finally:
        patch.undo()
