"""The pinned copy of beethoven-qa that every consuming repository carries (plan/REPO_SPLIT.md, 3).

Why a copy and not a dependency fetched from GitHub: the repositories are private, a GitHub Actions
run of one repository cannot read another private repository with its own token, and the grid's
image build has no GitHub token at all. A copy needs no secret anywhere, which is the project's
rule, and it is the pattern the launcher already uses for the site profiles. What keeps a copy
honest is its record: ``vendor/beethoven-qa/ORIGIN`` names the repository, the release and the
commit it was taken from and the sha256 of the copied tree, and ``vendor-check`` recomputes that
digest in the consumer's CI, so an edit to the copy or a kit file that leaves ORIGIN alone (drift,
a formatter, a hand fix) fails. It is a check in the consumer, not a proof: an edit that also
rewrites ORIGIN passes it, and so does an edit to the copy's own vendor.py, since the consumer's
``vendor-check`` is the copy's code checking itself. ``vendor-check --source <beethoven-qa
checkout>`` closes that gap where both checkouts are present, by rebuilding the digest from the
commit ORIGIN names; it is run from that checkout's own environment, never the consumer's. An
unedited copy refuses to run it from anywhere else; an edited one would not, which is exactly why
the line runs from beethoven-qa's own environment. A package of the same name ahead of the copy on
the consumer's PYTHONPATH shadows it for every check and for the gate: an unedited one refuses the
plain check (its code is in the consumer and not in the copy), an edited one is seen by neither
form, and a reviewer who sees ``src/beethoven_qa/`` in a consumer asks why.

Two commands, both run through ``python -m beethoven_qa``:

- ``vendor --into <consumer>`` runs from a clean beethoven-qa checkout and writes the copy (the
  package, the plugin and its marketplace file, and the repository kit: the pre-push hook, the
  auto-merge and rulesets workflows and the cloud-session hook every repository carries alike),
  read from the commit's own objects, never from the working tree. With ``--kit`` it also puts the
  kit files in place in the consumer. Before it writes anything it refuses a link anywhere on a
  path it would write, a file in the way of a directory, and any existing file it would replace
  whose bytes are not the ones HEAD holds for it (compared with the commit's blob, not with the
  index, so neither an ignore rule nor an index flag can hide an edit; a copy that still matches
  its ORIGIN is the exception). It never deletes: a file the new release no longer has is listed
  with a line for the person to run in a reviewed commit, and vendor-check fails until they have.
- ``vendor-check`` runs in the consumer (its CI and its gate) and fails on any difference between
  the copy and its ORIGIN, or between the live kit files and the copy's; on a byte-code cache that
  git tracks in the copy, or a hash-based one there (Python loads either in place of the source);
  and on a kit hook that git records without its executable bit, which git would skip.

The functions that carry contracts are pure; the ones that read, write and run git are private,
carry none, and are called by no contracted function. Both halves matter: the gate names only
contracted functions as targets, and CrossHair runs whatever a target calls, so an I/O helper a
contracted function called would be run with symbolic arguments after all.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict, NonNegativeInt, field_validator

from beethoven_qa.contracts import ensure, require

__all__ = [
    "COPY_ROOTS",
    "KIT",
    "KIT_EXECUTABLE",
    "ORIGIN_NAME",
    "REPOSITORY",
    "VENDOR_DIR",
    "Origin",
    "is_safe_relpath",
    "stale_files",
    "tree_digest",
]

REPOSITORY = "OpenSpineConsortium/beethoven-qa"
# Where the copy lives in a consumer, relative to its repository root.
VENDOR_DIR = "vendor/beethoven-qa"
ORIGIN_NAME = "ORIGIN"
# What the copy holds: the package (imported through the consumer's PYTHONPATH), the plugin and the
# marketplace file that lists it (so the copied directory is itself a plugin marketplace the image
# can register), and pyproject.toml for the package's declared dependencies.
COPY_ROOTS: tuple[str, ...] = ("pyproject.toml", "src/beethoven_qa", "plugin", ".claude-plugin")
# The repository kit: files every consuming repository carries byte for byte. They are copied under
# kit/ in the copy, and vendor-check compares the live files against them.
KIT: tuple[str, ...] = (
    ".githooks/pre-push",
    ".github/workflows/automerge.yml",
    ".github/workflows/rulesets.yml",
    ".claude/hooks/session-start.sh",
)
# The kit files git runs, so they must be executable: git skips a hook that is not, with only a
# hint. ORIGIN records no modes, so this is what the plain vendor-check knows of the release's.
KIT_EXECUTABLE: tuple[str, ...] = (".githooks/pre-push", ".claude/hooks/session-start.sh")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")


@beartype
@ensure(lambda result, path: not result or len(path) > 0, "never empty")
@ensure(lambda result, path: not result or not path.startswith("/"), "never absolute")
@ensure(lambda result, path: not result or "\\" not in path, "forward slashes only")
@ensure(
    lambda result, path: not result or all(part not in ("", ".", "..") for part in path.split("/")),
    "no empty, current or parent component, so it cannot leave the directory it is joined to",
)
def is_safe_relpath(path: str) -> bool:
    """``True`` when ``path`` is a relative path that stays inside the directory it is joined to."""
    if len(path) == 0 or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


@beartype
@require(
    lambda entries: all(is_safe_relpath(p) for p, _ in entries), "every path is a safe relpath"
)
@require(
    lambda entries: all(_HEX64.fullmatch(h) is not None for _, h in entries),
    "every file hash is a sha256 in lower-case hex",
)
@require(lambda entries: len({p for p, _ in entries}) == len(entries), "each path appears once")
@ensure(lambda result: _HEX64.fullmatch(result) is not None, "a sha256 in lower-case hex")
def tree_digest(entries: tuple[tuple[str, str], ...]) -> str:
    """The sha256 of a tree given as (path, sha256 of the file's bytes) pairs, in any order.

    Sorting first makes the digest a property of the tree, not of the order a directory walk
    happened to return. Read from the start, each entry is its path up to the first NUL (a safe
    path holds none), then exactly 64 hex characters and a newline, so two different trees never
    produce the same text.
    """
    text = "".join(f"{path}\x00{digest}\n" for path, digest in sorted(entries))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The precondition is the real domain (``_sync`` refuses any other name before it gets here), and it
# also keeps CrossHair 0.0.111 away from a fault of its own: sorting symbolic strings that hold NUL
# characters trips an internal ``assert False`` in its string comparison, reported as an exception
# in this function although the function is fine on those inputs (measured 2026-10-04).
@beartype
@require(
    lambda old, new: all(is_safe_relpath(p) for p in old | new),
    "file names of a copy, which are always safe relative paths",
)
@ensure(lambda result: list(result) == sorted(result), "listed in a stable order")
@ensure(lambda result, old, new: set(result) == old - new, "exactly the files the release dropped")
def stale_files(old: frozenset[str], new: frozenset[str]) -> list[str]:
    """Files present in the old copy that the new release no longer has."""
    return sorted(old - new)


class Origin(BaseModel):
    """The record of where a copy came from: the boundary where an ORIGIN file is read back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    ref: str
    commit: str
    tree_sha256: str
    files: NonNegativeInt

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if _HEX40.fullmatch(value) is None:
            raise ValueError("commit must be a full 40-character git SHA in lower-case hex")
        return value

    @field_validator("tree_sha256")
    @classmethod
    def _tree(cls, value: str) -> str:
        if _HEX64.fullmatch(value) is None:
            raise ValueError("tree_sha256 must be a sha256 in lower-case hex")
        return value

    @field_validator("repository", "ref")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if not value or "\n" in value or ":" in value:
            raise ValueError("must be one non-empty line without a colon")
        return value

    def render(self) -> str:
        return "".join(f"{key}: {value}\n" for key, value in self.model_dump().items())

    @classmethod
    def parse(cls, text: str) -> Origin:
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition(": ")
            if not sep or key in fields:
                raise ValueError(f"ORIGIN line {line!r} is not one 'key: value' pair")
            fields[key] = value
        return cls.model_validate(fields)


# --- I/O: private, uncontracted, never a gate target, called by no contracted function ------------

# The modes a copied file may have at the commit: a regular file, executable or not. A link (120000)
# or a submodule (160000) is refused; a copy holds files only.
_MODES = {"100644": 0o644, "100755": 0o755}


class _Refused(Exception):
    """A reason to write nothing, found before the first write."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Of the variables git lists as local to a repository, the two that carry configuration rather
# than name a repository: `git -c key=value` travels as GIT_CONFIG_PARAMETERS, and GIT_CONFIG_COUNT
# with its GIT_CONFIG_KEY_<n> and GIT_CONFIG_VALUE_<n> pairs is how a container lane commonly
# gives safe.directory. Both stay: they name no repository, and a lane that needs safe.directory
# that way would otherwise fail every git call here.
_GIT_CONFIG_ENV = frozenset({"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"})


@functools.cache
def _git_local_env() -> frozenset[str]:
    # The variables git treats as local to one repository (`git rev-parse --local-env-vars`:
    # GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_COMMON_DIR and the rest, the configuration
    # carriers above among them), asked of the git in use rather than kept as a list a newer git
    # could outgrow. The listing needs no repository.
    out = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
    )
    return frozenset(out.stdout.split())


def _git_env() -> dict[str, str]:
    # A hook runs with GIT_DIR in its environment (a linked worktree's pre-push hook with an
    # absolute one), and git honours it over -C: in such a hook `git -C <tmp> init` set
    # core.bare=true in the developer's own checkout, and `commit` and `tag` landed on their branch
    # (measured 2026-10-04, git 2.43). Without the variables that name a repository each call acts
    # on the checkout it names and no other; configuration given on the command line or through
    # the environment names none and is kept.
    dropped = _git_local_env() - _GIT_CONFIG_ENV
    return {key: value for key, value in os.environ.items() if key not in dropped}


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
        input=stdin,
    )
    return out.stdout


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, env=_git_env()
    ).stdout


def _toplevel(path: Path) -> Path | None:
    try:
        return Path(_git(path, "rev-parse", "--show-toplevel").strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None


def _hash_based_pyc(path: Path) -> bool:
    # PEP 552: bytes 4 to 8 of a .pyc are its flags, little-endian, and bit 0 set means hash-based.
    # Importing writes only timestamp pycs, so a hash-based one was compiled on purpose; and Python
    # loads one as it stands (unchecked) or after comparing the hash of the source alone (checked),
    # so either carries whatever code it was given and survives a fresh clone, where a timestamp
    # pyc is recompiled (measured 2026-10-04, Python 3.11). Unreadable counts, like anything else.
    try:
        with path.open("rb") as fh:
            header = fh.read(8)
    except OSError:
        return True
    return len(header) == 8 and bool(int.from_bytes(header[4:8], "little") & 0b01)


def _walk(root: Path) -> tuple[dict[str, Path], list[str]]:
    # Every regular file under root, by its path relative to root, and every link found (a copy
    # written by `vendor` never holds one). Python's own byte-code caches (timestamp .pyc files
    # directly inside a __pycache__ directory) are not part of a copy: importing the vendored
    # package creates them. Anything else in a __pycache__ directory, a hash-based pyc included,
    # is counted, so the cache cannot hide content.
    found: dict[str, Path] = {}
    links: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                links.append(full.relative_to(root).as_posix())
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            cache = Path(dirpath).name == "__pycache__" and name.endswith(".pyc")
            if cache and not _hash_based_pyc(full):
                continue
            found[full.relative_to(root).as_posix()] = full
    return found, sorted(links)


def _source_root() -> Path:
    # src/beethoven_qa/vendor.py -> the repository root of the checkout this module runs from.
    return Path(__file__).resolve().parents[2]


def _blobs(source: Path, commit: str) -> dict[str, tuple[int, bytes]]:
    # What a copy of ``commit`` holds, read from git's objects: COPY_ROOTS at their own paths and
    # the kit under kit/, each with the mode the commit gives it. The working tree is never read,
    # so neither a line-ending conversion nor an assume-unchanged edit can enter a copy.
    listing = _git_bytes(
        source, "ls-tree", "-r", "-z", "--full-tree", commit, "--", *COPY_ROOTS, *KIT
    )
    files: dict[str, tuple[int, bytes]] = {}
    for item in listing.split(b"\x00"):
        if not item:
            continue
        meta, _, raw = item.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Refused(f"a file name at {commit[:12]} is not UTF-8: {raw!r}") from exc
        if kind != "blob" or mode not in _MODES:
            raise _Refused(f"{path} is a {mode} entry at {commit[:12]} (a link or a submodule)")
        rel = f"kit/{path}" if path in KIT else path
        if not is_safe_relpath(rel):
            raise _Refused(f"refusing an unsafe path {rel!r}")
        files[rel] = (_MODES[mode], _git_bytes(source, "cat-file", "blob", oid))
    missing = [rel for rel in KIT if f"kit/{rel}" not in files]
    if missing:
        raise _Refused(f"the kit is incomplete at {commit[:12]}: {', '.join(missing)}")
    return files


def _blocked(base: Path, rel: str, directory: bool = False) -> str | None:
    # Why writing base/rel could reach somewhere else or replace the wrong thing, or None. ``base``
    # is a resolved directory; every component below it must be a real directory or not exist yet,
    # and the last one a regular file (a directory when ``directory``) or not exist yet.
    probe = base
    parts = rel.split("/")
    for i, part in enumerate(parts):
        probe = probe / part
        if probe.is_symlink():
            return f"{probe} is a link"
        if not probe.exists():
            return None
        last = i == len(parts) - 1
        if (not last or directory) and not probe.is_dir():
            return f"{probe} is a file where a directory belongs"
        if last and not directory and not probe.is_file():
            return f"{probe} is not a regular file"
    return None


def _committed(into: Path, paths: list[str]) -> set[str]:
    # The paths (relative to ``into``) that git tracks and whose bytes on disk are the ones HEAD
    # holds. Asked of git directly, file by file, so neither .gitignore nor
    # status.showUntrackedFiles can hide one; and compared with HEAD's own blob (hashed through the
    # filters `git add` would apply), never with the index, because `git diff` and `git status`
    # take an entry marked --assume-unchanged or --skip-worktree at its word and would report an
    # edit behind it as no change.
    if not paths:
        return set()
    tracked = sorted(
        set(_git(into, "--literal-pathspecs", "ls-files", "-z", "--", *paths).split("\x00")) - {""}
    )
    tracked = [rel for rel in tracked if "\n" not in rel]  # one path per line below
    try:
        _git(into, "rev-parse", "--verify", "--quiet", "HEAD")
    except subprocess.CalledProcessError:
        return set()  # nothing is committed yet
    if not tracked:
        return set()
    on_disk = _git(into, "hash-object", "--stdin-paths", stdin="".join(f"{p}\n" for p in tracked))
    in_head = _git(
        into,
        "cat-file",
        "--batch-check=%(objectname)",
        stdin="".join(f"HEAD:{p}\n" for p in tracked),
    )
    return {
        rel
        for rel, disk, head in zip(tracked, on_disk.splitlines(), in_head.splitlines(), strict=True)
        if disk == head
    }


def _write(path: Path, data: bytes, mode: int) -> None:
    # O_NOFOLLOW: a link that appeared after the checks is refused here rather than followed. The
    # mode is set on that same descriptor: a path-based chmod with follow_symlinks=False is
    # fchmodat(AT_SYMLINK_NOFOLLOW), which glibc before 2.32 (RHEL 8, the grid's CentOS 7) refuses
    # with ENOTSUP and CPython reports as NotImplementedError, after the first file was written.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as fh:
        os.fchmod(fd, mode)
        fh.write(data)


def _sync(into: Path, install_kit: bool, source: Path | None = None) -> int:
    # ``source`` is only ever given by the tests, which copy from a throwaway checkout.
    source = (source or _source_root()).resolve()
    if _toplevel(source) != source:
        print(
            f"vendor: {source} is not a beethoven-qa checkout (a vendored copy cannot copy itself)"
        )
        return 2
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        print("vendor: the beethoven-qa checkout has uncommitted changes; a copy is of a commit")
        return 2
    into = into.resolve()
    if _toplevel(into) != into:
        print(f"vendor: {into} is not the root of a git checkout")
        return 2
    if into == source:
        print("vendor: refusing to copy beethoven-qa into itself")
        return 2

    commit = _git(source, "rev-parse", "HEAD").strip()
    try:
        ref = _git(source, "describe", "--tags", "--exact-match", "HEAD").strip()
    except subprocess.CalledProcessError:
        ref = "untagged"
    dest = into / VENDOR_DIR
    try:
        files = _blobs(source, commit)
        origin = Origin(
            repository=REPOSITORY,
            ref=ref,
            commit=commit,
            tree_sha256=tree_digest(
                tuple((rel, _sha256(data)) for rel, (_, data) in files.items())
            ),
            files=len(files),
        )
        old = _existing(into, dest)
        kit = {rel: files[f"kit/{rel}"] for rel in KIT} if install_kit else {}
        _preflight(into, dest, files, kit, old)
    except _Refused as refusal:
        print(f"vendor: {refusal}")
        return 2
    except ValueError as exc:  # an Origin the record format cannot hold, such as a 64-hex commit
        print(f"vendor: this commit cannot be recorded in ORIGIN: {exc}")
        return 2

    try:
        for rel, (mode, data) in sorted(files.items()):
            _write(dest / rel, data, mode)
        _write(dest / ORIGIN_NAME, origin.render().encode("utf-8"), 0o644)
        print(f"vendor: {len(files)} files of {REPOSITORY} {ref} ({commit[:12]}) into {dest}")
        for rel, (mode, data) in kit.items():
            _write(into / rel, data, mode)
        if kit:
            print(f"vendor: kit files in place in {into}: {', '.join(KIT)}")
    except (OSError, NotImplementedError) as exc:
        # NotImplementedError is CPython's word for a file-system call the platform lacks.
        print(f"vendor: stopped part way ({exc}); `git status` shows what was written")
        return 3

    gone = stale_files(old, frozenset(files)) if old is not None else []
    if gone:
        paths = " ".join(shlex.quote(f"{VENDOR_DIR}/{rel}") for rel in gone)
        print("vendor: the new release no longer has these files; nothing was deleted. Remove them")
        print("        in a reviewed commit (this works whether or not they were committed), then")
        print("        run vendor-check:")
        print(f"        rm -- {paths} && git rm -q --cached --ignore-unmatch -- {paths}")
        return 1
    return 0


def _existing(into: Path, dest: Path) -> frozenset[str] | None:
    # The files of the copy already there, or None when there is none. A link anywhere in it, or a
    # name a copy can never hold, is refused: a copy written by `vendor` holds neither.
    reason = _blocked(into, VENDOR_DIR, directory=True)
    if reason is not None:
        raise _Refused(reason)
    if not dest.exists():
        return None
    present, links = _walk(dest)
    if links:
        raise _Refused(f"{dest} holds links a copy never has: {', '.join(links)}")
    old = frozenset(rel for rel in present if rel != ORIGIN_NAME)
    if not all(is_safe_relpath(rel) for rel in old):
        raise _Refused(f"{dest} holds a file name a copy can never hold; clean it up by hand")
    return old


def _preflight(
    into: Path,
    dest: Path,
    files: dict[str, tuple[int, bytes]],
    kit: dict[str, tuple[int, bytes]],
    old: frozenset[str] | None,
) -> None:
    # Every reason to write nothing, before the first write: a link or a file in the way on any path
    # to be written, and any existing file to be replaced that is not committed and unchanged.
    # Replacing is allowed without that only where nothing can be lost: a copy that still matches
    # its ORIGIN, and a kit file equal to that copy's kit file, are rewritten from a commit.
    targets = [f"{VENDOR_DIR}/{rel}" for rel in sorted(files)] + [f"{VENDOR_DIR}/{ORIGIN_NAME}"]
    targets += list(kit)
    for rel in targets:
        reason = _blocked(into, rel)
        if reason is not None:
            # A file the old copy had where the release now has a directory, or the reverse: the
            # old files in the way, for the person to remove in a reviewed commit.
            inside = rel.removeprefix(f"{VENDOR_DIR}/")
            way = sorted(
                r for r in old or () if inside.startswith(f"{r}/") or r.startswith(f"{inside}/")
            )
            hint = f"; remove {', '.join(way)} in a reviewed commit first" if way else ""
            raise _Refused(f"{reason}{hint}")
    pristine = old is not None and _pristine(dest)
    existing = [rel for rel in targets if (into / rel).is_file()]
    safe = _committed(into, existing)
    for rel in existing:
        if rel in safe:
            continue
        if pristine and rel.startswith(f"{VENDOR_DIR}/"):
            continue
        if pristine and rel in kit:
            kept = dest / "kit" / rel
            if kept.is_file() and (into / rel).read_bytes() == kept.read_bytes():
                continue
        raise _Refused(f"{rel} has uncommitted changes in {into}; commit or restore it first")


def _measure(dest: Path) -> tuple[str, int] | None:
    # The digest and file count of a copy as it is on disk, or None when it holds a link or a file
    # name a copy can never hold.
    present, links = _walk(dest)
    present.pop(ORIGIN_NAME, None)
    if links or not all(is_safe_relpath(rel) for rel in present):
        return None
    digest = tree_digest(tuple((rel, _sha256(p.read_bytes())) for rel, p in present.items()))
    return digest, len(present)


def _read_origin(dest: Path) -> Origin:
    path = dest / ORIGIN_NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path} is not a regular file")
    return Origin.parse(path.read_text(encoding="utf-8"))


def _pristine(dest: Path) -> bool:
    try:
        origin = _read_origin(dest)
    except (OSError, ValueError):
        return False
    return _measure(dest) == (origin.tree_sha256, origin.files)


def _index_modes(root: Path, paths: list[str]) -> dict[str, str]:
    # The mode git's index records for each of the paths it tracks (`ls-files -s`: mode, object,
    # stage, a tab, the path).
    listing = _git(root, "--literal-pathspecs", "ls-files", "-s", "-z", "--", *paths)
    modes: dict[str, str] = {}
    for item in listing.split("\x00"):
        if item:
            meta, _, path = item.partition("\t")
            modes[path] = meta.split(" ")[0]
    return modes


def _check(root: Path, source: Path | None = None) -> int:
    if source is not None:
        # The anchored check is only as good as the code running it, and the consumer's own
        # environment imports the copy: a copy whose vendor.py was edited to vouch for itself, with
        # ORIGIN untouched, would pass. So this form is run from the source checkout's own
        # environment (docs/CONSUMING.md), and an unedited copy refuses to run it from anywhere
        # else; an edited one would not, which is why the documented line never runs it here.
        source = source.resolve()
        if source != _source_root():
            print(
                f"vendor-check: --source {source} is not the checkout this command runs from "
                f"({_source_root()}); run the anchored check from that checkout's own environment, "
                "since a copy cannot vouch for itself"
            )
            return 1
    root = root.resolve()
    if _toplevel(root) != root:
        print(f"vendor-check: {root} is not the root of a git checkout")
        return 1
    dest = root / VENDOR_DIR
    reason = _blocked(root, VENDOR_DIR, directory=True)
    if reason is not None or not dest.is_dir():
        print(f"vendor-check: no copy at {VENDOR_DIR} in {root}{f' ({reason})' if reason else ''}")
        return 1
    # The plain check is the copy's own code, imported through the consumer's PYTHONPATH, and a
    # package of the same name in the consumer's own src/ comes first on that path: it would check
    # the copy in the copy's place, and the gate would run it too. An unedited one says so here;
    # an edited one is the limit the docstring states, the same as an edited copy.
    code = Path(__file__).resolve()
    if _toplevel(code.parent) == root and not code.is_relative_to(dest):
        print(
            f"vendor-check: this check is running {code}, which is in {root} but is not its copy: "
            f"a package named beethoven_qa ahead of {VENDOR_DIR}/src on the PYTHONPATH shadows the "
            "copy, for the gate too; remove it, or ask why it is there"
        )
        return 1
    try:
        origin = _read_origin(dest)
    except (OSError, ValueError) as exc:
        print(f"vendor-check: {VENDOR_DIR}/{ORIGIN_NAME} cannot be read: {exc}")
        return 1
    failures: list[str] = []
    if origin.repository != REPOSITORY:
        failures.append(f"ORIGIN names {origin.repository}, not {REPOSITORY}")
    measured = _measure(dest)
    if measured is None:
        failures.append("the copy holds a link or a file name a copy can never hold")
    elif measured != (origin.tree_sha256, origin.files):
        digest, count = measured
        failures.append(
            f"the copy differs from its ORIGIN ({count} files, tree {digest[:12]}; ORIGIN "
            f"says {origin.files} files, tree {origin.tree_sha256[:12]}): it was edited by hand or "
            "a file the release dropped is still there"
        )
    for rel in KIT:
        live, kept = root / rel, dest / "kit" / rel
        if live.is_symlink() or not live.is_file():
            failures.append(f"kit file {rel} is missing (python -m beethoven_qa vendor --kit)")
        elif not kept.is_file() or live.read_bytes() != kept.read_bytes():
            failures.append(f"kit file {rel} differs from the copy of {origin.ref}")
        elif os.access(live, os.X_OK) != os.access(kept, os.X_OK):
            # git skips a hook that is not executable, without a word: the gate would not run.
            failures.append(f"kit file {rel} is executable in one place and not the other")
    # What git records is what every clone gets, whatever this disk says: a checkout with
    # core.fileMode=false (WSL on /mnt/c, NTFS) commits a new 0755 hook as 100644, and the two
    # files above then agree with each other on every clone, both without the bit.
    hooks = [*KIT_EXECUTABLE, *(f"{VENDOR_DIR}/kit/{rel}" for rel in KIT_EXECUTABLE)]
    recorded = _index_modes(root, hooks)
    unset = [rel for rel in hooks if rel in recorded and recorded[rel] != "100755"]
    if unset:
        paths = " ".join(shlex.quote(rel) for rel in unset)
        failures.append(
            "git records these without the executable bit, so every clone gets a hook git skips "
            "(core.fileMode=false does that); fix the record: "
            f"git update-index --chmod=+x -- {paths}"
        )
    # A byte-code cache git tracks reaches every clone, and Python can load it in place of the
    # source (a hash-based one without looking at the source); the digest above leaves Python's
    # own timestamp caches out, so a tracked one is refused by name.
    tracked = _git(root, "--literal-pathspecs", "ls-files", "-z", "--", VENDOR_DIR).split("\x00")
    caches = sorted(rel for rel in tracked if "__pycache__" in rel.split("/"))
    if caches:
        paths = " ".join(shlex.quote(rel) for rel in caches)
        failures.append(
            "git tracks byte-code caches in the copy, which Python can load in place of the "
            f"source; remove them in a reviewed commit: rm -- {paths} && "
            f"git rm -q --cached -- {paths}"
        )
    if source is not None:
        failures += _against(source, dest, origin)
    for failure in failures:
        print(f"vendor-check: {failure}")
    if not failures:
        anchored = " and the commit it names" if source is not None else ""
        print(
            f"vendor-check: {VENDOR_DIR} matches its ORIGIN{anchored} "
            f"({origin.repository} {origin.ref}, {origin.commit[:12]})"
        )
    return 1 if failures else 0


def _against(source: Path, dest: Path, origin: Origin) -> list[str]:
    # The copy rebuilt from the commit ORIGIN names, in a beethoven-qa checkout: the anchor that a
    # rewritten ORIGIN cannot forge.
    try:
        _git(source, "cat-file", "-e", f"{origin.commit}^{{commit}}")
    except subprocess.CalledProcessError:
        return [f"{source} has no commit {origin.commit[:12]}; fetch it, or the ORIGIN is wrong"]
    try:
        files = _blobs(source, origin.commit)
    except _Refused as refusal:
        return [str(refusal)]
    failures: list[str] = []
    digest = tree_digest(tuple((rel, _sha256(data)) for rel, (_, data) in files.items()))
    if (digest, len(files)) != (origin.tree_sha256, origin.files):
        failures.append(
            f"ORIGIN's tree {origin.tree_sha256[:12]} is not the tree of commit "
            f"{origin.commit[:12]} ({digest[:12]})"
        )
    if origin.ref != "untagged":
        try:
            tagged = _git(source, "rev-parse", "--verify", f"{origin.ref}^{{commit}}").strip()
        except subprocess.CalledProcessError:
            tagged = ""
        if tagged != origin.commit:
            failures.append(f"{origin.ref} in {source} is not commit {origin.commit[:12]}")
    for rel, (mode, _) in files.items():
        on_disk = dest / rel
        if on_disk.is_file() and os.access(on_disk, os.X_OK) != bool(mode & 0o111):
            failures.append(f"{rel} is executable in one place and not the other")
    return failures
