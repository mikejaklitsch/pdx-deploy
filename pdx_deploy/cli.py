"""Deploy a Paradox dev mod to its game-readable location.

Auto-detects the mod root via .metadata/, reads the deploy name from
metadata.json (stripping " (Dev)"), and syncs game directories to the
deploy root (the parent of .dev-mods/). Only changed files are copied.

Usage:
    pdx-deploy                  # deploy current directory
    pdx-deploy /path/to/mod     # deploy specific mod
    pdx-deploy --no-lint        # skip lint checks
    pdx-deploy --full           # wipe the deployed mod and copy everything
    pdx-deploy --exclude PAT    # leave out files matching PAT (repeatable)
    pdx-deploy --exclude-from F # leave out the patterns in F, one per line
    pdx-deploy lock [path]      # block deploys; make dev game dirs read-only
    pdx-deploy unlock [path]    # remove the lock and restore write access
    pdx-deploy status [path]    # show lock state

An exclude pattern is a glob on the path relative to the mod root, such as
in_game/common/on_action/* or main_menu/setup. A directory pattern covers
everything under it. Excluded files are not copied, and their deployed
copies are removed; the next deploy without the pattern restores them.
"""

import fnmatch
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MOD_DIRS = [".metadata", "in_game", "loading_screen", "main_menu"]
SKIP_EXTENSIONS = {".md"}
LOCK_REGISTRY = Path(__file__).resolve().parent.parent / ".deploy-locks.json"
MANIFEST_DIR = Path.home() / ".cache" / "pdx-deploy" / "manifests"
LIST_LIMIT = 20


def load_locks():
    if LOCK_REGISTRY.exists():
        return json.loads(LOCK_REGISTRY.read_text(encoding="utf-8"))
    return {}


def save_locks(locks):
    LOCK_REGISTRY.write_text(
        json.dumps(locks, indent=2) + "\n", encoding="utf-8"
    )


def _win_tools():
    icacls = shutil.which("icacls.exe")
    wslpath = shutil.which("wslpath")
    whoami = shutil.which("whoami.exe")
    if icacls and wslpath and whoami:
        return icacls, wslpath, whoami
    return None


def set_dev_write_protection(mod_root, protect):
    """Deny (or restore) write and delete on the mod's game directories.

    Uses a Windows deny ACL so writes are blocked from both Windows and
    WSL. Returns True when every existing game directory was processed.
    """
    tools = _win_tools()
    if not tools:
        print("  WARNING: icacls not available; dev directories NOT write-protected.")
        return False
    icacls, wslpath, whoami = tools
    user = subprocess.run([whoami], capture_output=True, text=True).stdout.strip()
    if not user:
        print("  WARNING: could not resolve Windows user; skipping write protection.")
        return False
    ok = True
    for d in MOD_DIRS:
        path = mod_root / d
        if not path.exists():
            continue
        win_path = subprocess.run(
            [wslpath, "-w", str(path)], capture_output=True, text=True
        ).stdout.strip()
        if protect:
            cmd = [icacls, win_path, "/deny", f"{user}:(OI)(CI)(WD,AD,DC,DE)"]
        else:
            cmd = [icacls, win_path, "/remove:d", user]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  WARNING: icacls failed on {d}: {result.stderr.strip()}")
            ok = False
    return ok


def resolve_mod_root(args):
    if args:
        mod_root = Path(args[0]).resolve()
        if not (mod_root / ".metadata").is_dir():
            print(f"Error: {mod_root} has no .metadata/ directory")
            sys.exit(1)
        return mod_root
    mod_root = find_mod_root()
    if not mod_root:
        print("Error: no .metadata/ found in current directory or parents")
        sys.exit(1)
    return mod_root


def get_deploy_target(mod_root):
    deploy_root = find_deploy_root(mod_root)
    if not deploy_root:
        return None
    name = get_deploy_name(mod_root)
    if not name:
        name = mod_root.name.removesuffix(" Development")
    return str((deploy_root / name).resolve())


def cmd_lock(args):
    mod_root = resolve_mod_root(args)
    locks = load_locks()
    src = str(mod_root)

    if src in locks:
        print(f"Already locked: {mod_root.name}")
        if not locks[src].get("readonly"):
            if set_dev_write_protection(mod_root, True):
                locks[src]["readonly"] = True
                save_locks(locks)
                print(f"  Dev game directories are now read-only until unlocked.")
        return

    target = get_deploy_target(mod_root)
    entry = {"target": target}
    if set_dev_write_protection(mod_root, True):
        entry["readonly"] = True
    locks[src] = entry
    save_locks(locks)

    print(f"Locked: {mod_root.name}")
    if target:
        print(f"  Target: {target}")
    print(f"  Deploys from or to this mod are blocked until unlocked.")
    if entry.get("readonly"):
        print(f"  Dev game directories are read-only until unlocked.")


def cmd_unlock(args):
    mod_root = resolve_mod_root(args)
    locks = load_locks()
    src = str(mod_root)

    if src not in locks:
        print(f"Not locked: {mod_root.name}")
        return

    if locks[src].get("readonly"):
        if set_dev_write_protection(mod_root, False):
            print(f"  Dev game directories are writable again.")

    del locks[src]
    save_locks(locks)
    print(f"Unlocked: {mod_root.name}")


def cmd_status(args):
    mod_root = resolve_mod_root(args)
    locks = load_locks()
    src = str(mod_root)

    if src in locks:
        entry = locks[src]
        print(f"LOCKED: {mod_root.name}")
        if entry.get("target"):
            print(f"  Target: {entry['target']}")
        if entry.get("readonly"):
            print(f"  Dev game directories are read-only.")
    else:
        print(f"Unlocked: {mod_root.name}")


def check_locks(mod_root, dst):
    locks = load_locks()
    src = str(mod_root)
    dst_str = str(dst.resolve())

    if src in locks:
        print(f"  DEPLOY BLOCKED: {mod_root.name} is locked.")
        print(f"  The lock likely exists to prevent overwriting a deployed")
        print(f"  directory that is ahead of this dev source, usually from")
        print(f"  GUI editing done directly in the deployed mod.")
        print(f"  Use 'pdx-deploy unlock' to remove the lock when ready.")
        return False

    for locked_src, entry in locks.items():
        if entry.get("target") == dst_str and locked_src != src:
            locked_name = Path(locked_src).name
            print(f"  DEPLOY BLOCKED: target directory is locked by {locked_name}.")
            print(f"  The lock likely exists to prevent overwriting a deployed")
            print(f"  directory that is ahead of its dev source, usually from")
            print(f"  GUI editing done directly in the deployed mod.")
            print(f"  Unlock {locked_name} first with:")
            print(f"    pdx-deploy unlock {locked_src}")
            return False

    return True


def find_mod_root(start=None):
    p = (start or Path.cwd()).resolve()
    while True:
        if (p / ".metadata").is_dir():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


def find_deploy_root(mod_root):
    """Walk up from mod_root looking for a parent named .dev-mods."""
    p = mod_root
    while True:
        if p.parent.name == ".dev-mods":
            return p.parent.parent
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


def get_deploy_name(mod_root):
    meta_file = mod_root / ".metadata" / "metadata.json"
    if not meta_file.exists():
        return None
    raw = meta_file.read_text(encoding="utf-8-sig")
    meta = json.loads(raw)
    name = meta.get("name", "")
    if not name:
        return None
    return name.replace(" (Dev)", "")


def run_lint(src):
    lint_dir = src / "tools" / "lint"
    if not lint_dir.is_dir():
        return True
    try:
        tools_dir = str(src / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import lint
        importlib.reload(lint)
        results = lint.run_all(src)
        if tools_dir in sys.path:
            sys.path.remove(tools_dir)
    except Exception as e:
        print(f"  LINT ERROR: {e}")
        return True
    if not results:
        print(f"  Lint: all checks passed")
        return True
    total = 0
    for name, errors in sorted(results.items()):
        print(f"  LINT [{name}] ({len(errors)} issue{'s' if len(errors) != 1 else ''})")
        for err in errors:
            print(f"    {err}")
        total += len(errors)
    print(f"  Lint: {total} issue{'s' if total != 1 else ''} found")
    return False


METADATA_REL = ".metadata/metadata.json"


def _stat_key(st):
    return [st.st_size, st.st_mtime_ns]


def manifest_path(dst):
    digest = hashlib.sha1(str(dst.resolve()).encode("utf-8")).hexdigest()
    return MANIFEST_DIR / f"{digest}.json"


def load_manifest(mod_root, dst):
    """Return {relpath: [src_size, src_mtime, dst_size, dst_mtime]} from the last deploy."""
    path = manifest_path(dst)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if data.get("src") != str(mod_root):
        return {}
    return data.get("files", {})


def save_manifest(mod_root, dst, files):
    path = manifest_path(dst)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"src": str(mod_root), "dst": str(dst.resolve()), "files": files}
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def is_excluded(rel, excludes):
    """True when rel matches a pattern, or lies under a directory pattern."""
    for pat in excludes:
        pat = pat.strip("/")
        if fnmatch.fnmatchcase(rel, pat) or rel.startswith(pat + "/"):
            return True
    return False


def scan_source(mod_root, src_dirs, excludes=()):
    """Map each deployable file's relative path to its source stat."""
    files = {}
    for d in src_dirs:
        for dirpath, _, filenames in os.walk(mod_root / d):
            for f in filenames:
                if Path(f).suffix.lower() in SKIP_EXTENSIONS:
                    continue
                full = Path(dirpath) / f
                rel = full.relative_to(mod_root).as_posix()
                if rel != METADATA_REL and not is_excluded(rel, excludes):
                    files[rel] = full.stat()
    return files


def remove_stale(dst, keep):
    """Delete anything under dst that the current source does not produce."""
    removed = []
    if not dst.is_dir():
        return removed
    for dirpath, dirnames, filenames in os.walk(dst, topdown=False):
        base = Path(dirpath)
        for f in filenames:
            full = base / f
            if full.relative_to(dst).as_posix() not in keep:
                full.unlink()
                removed.append(full.relative_to(dst).as_posix())
        if base != dst and not any(base.iterdir()):
            base.rmdir()
    return removed


def sync_python(mod_root, dst, src_dirs, full, excludes=()):
    """Copy files whose source or deployed copy changed since the last deploy.

    Fallback for when robocopy is unavailable. drvfs truncates copied
    mtimes to whole seconds, so source and deployed stats cannot be
    compared directly; a manifest records both sides after each deploy.
    """
    src_files = scan_source(mod_root, src_dirs, excludes)
    manifest = {} if full else load_manifest(mod_root, dst)
    removed = remove_stale(dst, set(src_files) | {METADATA_REL})
    changed = []
    for rel, src_st in src_files.items():
        target = dst / rel
        prev = manifest.get(rel)
        if prev and prev[:2] == _stat_key(src_st):
            try:
                if prev[2:] == _stat_key(target.stat()):
                    continue
            except FileNotFoundError:
                pass
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mod_root / rel, target)
        changed.append(rel)
    save_manifest(mod_root, dst, {
        rel: _stat_key(src_st) + _stat_key((dst / rel).stat())
        for rel, src_st in src_files.items()
    })
    return changed, removed


def _robocopy_tools(mod_root, dst):
    """Return (robocopy, wslpath) when both paths live on a Windows drive."""
    robocopy = shutil.which("robocopy.exe")
    wslpath = shutil.which("wslpath")
    if not (robocopy and wslpath):
        return None
    if not all(str(p).startswith("/mnt/") for p in (mod_root, dst)):
        return None
    return robocopy, wslpath


def _win_path(wslpath, path):
    return subprocess.run(
        [wslpath, "-w", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()


def sync_robocopy(mod_root, dst, src_dirs, tools):
    """Mirror each game directory with robocopy, which compares size and
    timestamp natively on NTFS instead of stat-ing every file through drvfs."""
    robocopy, wslpath = tools
    changed, removed = [], []

    dst.mkdir(parents=True, exist_ok=True)
    for entry in dst.iterdir():
        if entry.name not in src_dirs:
            if entry.is_dir():
                shutil.rmtree(entry)
                removed.append(entry.name + "/")
            else:
                entry.unlink()
                removed.append(entry.name)

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "robocopy.log"
        win_log = _win_path(wslpath, log)
        for d in src_dirs:
            win_src = _win_path(wslpath, mod_root / d)
            win_dst = _win_path(wslpath, dst / d)
            excludes = ["*.md"]
            if d == ".metadata":
                # Full path, so only the top-level file is excluded; a bare
                # name would also skip same-named files in subdirectories.
                excludes.append(win_src + "\\metadata.json")
            cmd = [
                robocopy, win_src, win_dst, "/MIR", "/XF", *excludes,
                "/R:1", "/W:1", "/MT:8", "/NJH", "/NJS", "/NP", "/NS",
                "/NDL", "/FP", f"/UNILOG:{win_log}",
            ]
            result = subprocess.run(cmd, capture_output=True)
            text = log.read_text(encoding="utf-16") if log.exists() else ""
            # robocopy exit codes below 8 are success bitmasks.
            if result.returncode >= 8:
                print(f"  ERROR: robocopy failed on {d} (exit {result.returncode})")
                for line in text.splitlines()[-10:]:
                    print(f"    {line.strip()}")
                return None
            for line in text.splitlines():
                fields = [f.strip() for f in line.split("\t") if f.strip()]
                if len(fields) < 2:
                    continue
                kind, path = fields[0], fields[-1]
                base = win_dst if kind.startswith("*EXTRA") else win_src
                if not path.lower().startswith(base.lower()):
                    continue
                rel = d + path[len(base):].replace("\\", "/")
                (removed if kind.startswith("*EXTRA") else changed).append(rel)

    # /XF also shields matching destination files from /MIR's purge, so
    # stray .md files in the deployed copy are removed here instead.
    for d in src_dirs:
        for dirpath, _, filenames in os.walk(dst / d):
            for f in filenames:
                if Path(f).suffix.lower() in SKIP_EXTENSIONS:
                    full = Path(dirpath) / f
                    full.unlink()
                    removed.append(full.relative_to(dst).as_posix())
    return changed, removed


def deploy_metadata(mod_root, dst):
    """Write metadata.json with " (Dev)" stripped, only when it differs."""
    src_file = mod_root / METADATA_REL
    if not src_file.exists():
        return None, False
    raw = src_file.read_bytes()
    meta = json.loads(raw.decode("utf-8-sig"))
    if "(Dev)" in meta.get("name", ""):
        meta["name"] = meta["name"].replace(" (Dev)", "")
        raw = (json.dumps(meta, indent=4) + "\n").encode("utf-8-sig")
    dst_file = dst / METADATA_REL
    if dst_file.exists() and dst_file.read_bytes() == raw:
        return meta, False
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    dst_file.write_bytes(raw)
    return meta, True


def print_paths(label, paths):
    print(f"  {label}: {len(paths)}")
    for rel in sorted(paths)[:LIST_LIMIT]:
        print(f"    {rel}")
    if len(paths) > LIST_LIMIT:
        print(f"    ... and {len(paths) - LIST_LIMIT} more")


def deploy(mod_root, skip_lint=False, full=False, excludes=()):
    deploy_root = find_deploy_root(mod_root)
    if not deploy_root:
        print(f"Error: could not find .dev-mods/ ancestor for {mod_root}")
        print("The mod must be inside a .dev-mods/ directory.")
        sys.exit(1)

    name = get_deploy_name(mod_root)
    if not name:
        name = mod_root.name.removesuffix(" Development")

    dst = deploy_root / name

    print(f"Deploying: {name}")

    if not check_locks(mod_root, dst):
        return None

    if not skip_lint:
        if not run_lint(mod_root):
            print(f"  Deploy blocked by lint errors. Use --no-lint to skip.")
            return None

    src_dirs = [d for d in MOD_DIRS if (mod_root / d).is_dir()]
    if not src_dirs:
        print(f"  WARNING: No game directories found in {mod_root}")
        return None

    fallback_name = mod_root.name.removesuffix(" Development")
    fallback_dst = deploy_root / fallback_name
    stale_dsts = {fallback_dst} if fallback_dst != dst else set()
    if full:
        stale_dsts.add(dst)
    for old_dst in stale_dsts:
        if old_dst.exists():
            shutil.rmtree(old_dst)
            print(f"  Cleaned: {old_dst.name}/")

    # robocopy /XF cannot take a long path list, so excludes use the Python sync
    tools = None if excludes else _robocopy_tools(mod_root, dst)
    if excludes:
        print(f"  Excluding: {', '.join(excludes)}")
    if tools:
        result = sync_robocopy(mod_root, dst, src_dirs, tools)
        if result is None:
            return None
        changed, removed = result
    else:
        changed, removed = sync_python(mod_root, dst, src_dirs, full, excludes)

    meta, meta_written = deploy_metadata(mod_root, dst)
    if meta_written:
        changed.append(METADATA_REL)

    if changed:
        print_paths("Copied", changed)
    if removed:
        print_paths("Removed", removed)
    if not changed and not removed:
        print(f"  Up to date")

    hook = mod_root / "tools" / "post_deploy.py"
    if hook.exists():
        spec = importlib.util.spec_from_file_location("post_deploy", hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "post_deploy"):
            mod.post_deploy(mod_root, dst, meta)
            print(f"  Ran post_deploy hook")

    print(f"  Done: {dst}")
    return dst


def main():
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        sys.exit(0)

    subcommands = {"lock": cmd_lock, "unlock": cmd_unlock, "status": cmd_status}
    if args and args[0] in subcommands:
        subcommands[args[0]](args[1:])
        return

    excludes, rest = [], []
    it = iter(args)
    for a in it:
        if a in ("--exclude", "--exclude-from"):
            value = next(it, None)
            if value is None:
                print(f"Error: {a} needs a value")
                sys.exit(1)
            if a == "--exclude":
                excludes.append(value)
            else:
                lines = Path(value).read_text(encoding="utf-8").splitlines()
                excludes += [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]
        else:
            rest.append(a)
    args = rest

    skip_lint = "--no-lint" in args
    full = "--full" in args
    args = [a for a in args if a not in ("--no-lint", "--full")]

    mod_root = resolve_mod_root(args)
    deploy(mod_root, skip_lint=skip_lint, full=full, excludes=excludes)


if __name__ == "__main__":
    main()
