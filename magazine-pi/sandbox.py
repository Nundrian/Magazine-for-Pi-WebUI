"""Kernel-enforced confinement for Magazine workers (Linux Landlock + seccomp).

Magazine, Pi and every worker run as the same unprivileged container user, so
ordinary permissions cannot separate them. Landlock is an unprivileged Linux
security module: a process restricts itself and every descendant it will ever
start, irreversibly. The kernel checks the object actually opened, so symbolic
links, ``..`` traversal, hard links, renames and shell redirection cannot reach
a path the ruleset does not grant.

Two layers are used:

* **Worker domain** (``--policy``): applied by this launcher before Node starts.
  Node and the SDK may write the project workspace, the private scratch folder,
  this attempt's session and publication folders and operator-listed folders.
  Every grant is verified on the opened descriptor against the real path the
  parent expected, so a symbolic link swapped in after validation is refused.
* **Tool domain** (``--nest``): every shell command a model runs re-executes
  itself here first (see ``TOOL_PREFIX``). It is a nested Landlock domain, so
  tool processes cannot open the worker's ``/proc/<pid>/fd`` (its channel to
  Magazine), and it removes write access to the attempt's session and
  publication folders: only Magazine's own publisher code can write those.

A seccomp filter refuses ``socket(AF_UNIX, ...)``, datagram ``socketpair`` (so
named sockets, including Pi's control socket that hands out the host token,
cannot be reached), io_uring and foreign-architecture system calls.

The launcher stays as a sandboxed child subreaper: when Node exits, or Magazine
asks it to stop, every remaining descendant (including ones that called
``setsid``) is killed before the launcher exits.

Not covered by any of this: changing permissions or timestamps of files the
worker may not open (Magazine seals and heals those itself), network access
(Landlock network rules need ABI 4), and signals to same-user processes outside
the sandbox (needs ABI 6, applied automatically when available).
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import signal
import stat
import sys

__all__ = ["abi_version", "SandboxError", "restrict", "nest", "describe", "MIN_ABI", "TOOL_PREFIX"]

MIN_ABI = 3  # truncate(2) is only governed from ABI 3

_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_CHILD_SUBREAPER = 36
_PR_SET_PDEATHSIG = 1
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_SCOPE_ABSTRACT_UNIX, _SCOPE_SIGNAL = 1 << 0, 1 << 1  # ABI 6

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13      # ABI 2
TRUNCATE = 1 << 14   # ABI 3

READ = READ_FILE | READ_DIR
_WRITE_BASE = (WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR | MAKE_REG
               | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM)
_FILE_ONLY = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE  # rights valid on a non-directory

# Prepended by the worker to every shell command (Pi SDK "shellCommandPrefix").
# The outer shell immediately re-executes the whole command string inside the
# nested tool domain; if that cannot happen it exits instead of running it.
TOOL_PREFIX = ('[ -n "$MAGAZINE_TOOL_DOMAIN" ] || { exec "$MAGAZINE_SANDBOX_PYTHON" -I "$MAGAZINE_SANDBOX_LAUNCHER" '
               '--nest -- "$BASH" -c "$BASH_EXECUTION_STRING"; exit 126; }')


class SandboxError(RuntimeError):
    """Confinement could not be established; the worker must not start."""


_libc = None


def _syscall():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.syscall.restype = ctypes.c_long
    return _libc


def abi_version() -> int:
    """Landlock ABI supported by the running kernel, or 0 if unavailable.

    ``MAGAZINE_LANDLOCK_MAX_ABI`` lowers it (never raises it), so a newer test
    kernel can reproduce the behaviour of an older production kernel.
    """
    if not sys.platform.startswith("linux"):
        return 0
    try:
        value = _syscall().syscall(_SYS_CREATE, None, ctypes.c_size_t(0),
                                   ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    value = int(value) if value > 0 else 0
    cap = os.environ.get("MAGAZINE_LANDLOCK_MAX_ABI", "")
    return min(value, int(cap)) if cap.isdigit() else value


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _write_rights(abi: int) -> int:
    rights = _WRITE_BASE
    if abi >= 2:
        rights |= REFER
    if abi >= 3:
        rights |= TRUNCATE
    return rights


def _real(path: str) -> str:
    return os.path.realpath(path)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _entry(item):
    """Grant entries are ``{"path", "real", "protected_ok"}`` or a bare path."""
    if isinstance(item, str):
        return {"path": item, "real": None, "protected_ok": False}
    return {"path": item["path"], "real": item.get("real"), "protected_ok": bool(item.get("protected_ok"))}


def _open_verified(item, protected, required):
    """O_PATH descriptor for a grant, verified on the descriptor itself."""
    try:
        fd = os.open(item["path"], os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        if required:
            raise SandboxError(f"sandbox path is missing: {item['path']}") from None
        return None
    try:
        actual = os.readlink(f"/proc/self/fd/{fd}")
        if item["real"] is not None and actual != item["real"]:
            raise SandboxError(f"sandbox path {item['path']} changed after validation (now {actual}); refused")
        for root in protected:
            if _within(root, actual) and actual != root:
                raise SandboxError(f"sandbox grant {actual} would contain protected state {root}; refused")
            if _within(actual, root) and not item["protected_ok"]:
                raise SandboxError(f"sandbox grant {actual} is inside protected state {root}; refused")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _ruleset(handled, scoped, abi):
    lib = _syscall()
    attr = _RulesetAttr(handled, 0, scoped)
    size = ctypes.sizeof(attr) if abi >= 6 else 8
    fd = lib.syscall(_SYS_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise SandboxError(f"Landlock ruleset refused: {os.strerror(ctypes.get_errno())}")
    return fd


def _add_rule(ruleset_fd, path_fd, access, handled):
    if not stat.S_ISDIR(os.fstat(path_fd).st_mode):
        access &= _FILE_ONLY
    access &= handled
    if not access:
        return False
    attr = _PathBeneathAttr(access, path_fd)
    if _syscall().syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset_fd), ctypes.c_int(_RULE_PATH_BENEATH),
                          ctypes.byref(attr), ctypes.c_uint32(0)) != 0:
        raise SandboxError(f"Landlock rule refused: {os.strerror(ctypes.get_errno())}")
    return True


def _enforce(ruleset_fd):
    lib = _syscall()
    if lib.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxError(f"no_new_privs refused: {os.strerror(ctypes.get_errno())}")
    if lib.syscall(_SYS_RESTRICT, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)) != 0:
        raise SandboxError(f"Landlock restriction refused: {os.strerror(ctypes.get_errno())}")


# Descriptors of the granted paths, kept open (close-on-exec) by the process
# that applied the rules: the launcher, which supervises the worker for its
# whole life. Landlock attaches a rule to an inode. On filesystems whose inodes
# are not stable -- Docker Desktop's drvfs/9p bind mounts of Windows folders
# (cache=5) -- a directory is given a fresh inode once nothing references it,
# and a rule on it would silently stop matching: the worker could not even
# read its own code. Holding the descriptor keeps the directory (and its
# ancestors) cached, so every later lookup reaches the inode the rule is on.
_PINNED: list[int] = []


def _room_for(count):
    """Raise the soft descriptor limit so every grant can stay pinned."""
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    need = count + len(_PINNED) + 64
    if soft != resource.RLIM_INFINITY and soft < need:
        if hard != resource.RLIM_INFINITY and hard < need:
            raise SandboxError(f"the sandbox needs {need} open files for {count} grants but the hard limit is "
                               f"{hard}; raise the container's nofile limit")
        resource.setrlimit(resource.RLIMIT_NOFILE, (need, hard))


def _apply(grants_read, grants_write, write_only, protected, required, abi):
    if write_only:
        handled, write_access = _write_rights(abi), _write_rights(abi)
    else:
        handled = READ | _write_rights(abi)
        write_access = READ | _write_rights(abi)
    scoped = (_SCOPE_SIGNAL | _SCOPE_ABSTRACT_UNIX) if abi >= 6 else 0
    _room_for(len(grants_read) + len(grants_write))
    ruleset_fd = _ruleset(handled, scoped, abi)
    applied = {"read": [], "write": []}
    try:
        for kind, items, access in (("read", grants_read, READ), ("write", grants_write, write_access)):
            for raw in items:
                item = _entry(raw)
                fd = _open_verified(item, protected, item["path"] in required)
                if fd is None:
                    continue
                added = False
                try:
                    added = _add_rule(ruleset_fd, fd, access, handled)
                    if added:
                        applied[kind].append(item["path"])
                finally:
                    if added:
                        _PINNED.append(fd)  # see _PINNED: keeps the rule's inode reachable
                    else:
                        os.close(fd)
        _enforce(ruleset_fd)
    finally:
        os.close(ruleset_fd)
    return applied


def restrict(policy: dict) -> dict:
    """Irreversibly confine this process (and future children) to *policy*.

    ``read``/``write``: grant entries; ``protected``: real paths that no grant
    may contain or (unless ``protected_ok``) lie inside; ``required``: paths
    that must exist; ``min_abi`` (default 3).
    """
    abi = abi_version()
    if abi < 1:
        raise SandboxError("Linux Landlock is unavailable on this kernel/container")
    if abi < int(policy.get("min_abi", MIN_ABI)):
        raise SandboxError(f"Landlock ABI {abi} cannot govern truncation; ABI {MIN_ABI} or newer is required")
    protected = [_real(p) for p in policy.get("protected", [])]
    applied = _apply(policy.get("read", []), policy.get("write", []), False, protected,
                     set(policy.get("required", [])), abi)
    if policy.get("deny_unix_sockets", True):
        deny_unix_sockets()
    return {"mechanism": "landlock", "abi": abi, "unix_sockets": not policy.get("deny_unix_sockets", True),
            "signals_scoped": abi >= 6, **applied}


def nest(policy: dict) -> dict:
    """Add the tool domain: a nested layer with a narrower set of write grants.

    Its descriptors close when the tool command is exec'd; its rules keep
    matching because every tool grant is also a worker grant, pinned by the
    supervising launcher (``PiDispatch._policy`` enforces that).
    """
    abi = abi_version()
    if abi < MIN_ABI:
        raise SandboxError("Landlock unavailable for the tool domain")
    protected = [_real(p) for p in policy.get("protected", [])]
    return _apply([], policy.get("write", []), True, protected, set(), abi)


# ------------------------------------------------------------------ seccomp
_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 22, 2
_RET_ALLOW, _RET_ERRNO = 0x7FFF0000, 0x00050000
# (audit arch, socket, socketpair, io_uring_setup)
_ARCH = {"x86_64": (0xC000003E, 41, 53, 425), "aarch64": (0xC00000B7, 198, 199, 425)}
_AF_UNIX, _SOCK_DGRAM, _SOCK_TYPE_MASK = 1, 2, 0xF


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


def _seccomp_program(machine: str):
    if machine not in _ARCH:
        raise SandboxError(f"seccomp socket filter is not defined for {machine}")
    arch, nr_socket, nr_socketpair, nr_uring = _ARCH[machine]
    ld, jeq, jge, ret, alu_and = 0x20, 0x15, 0x35, 0x06, 0x54
    deny = _RET_ERRNO | errno.EACCES
    return [
        (ld, 0, 0, 4),                     # 0  arch
        (jeq, 1, 0, arch),                 # 1  native -> 3
        (ret, 0, 0, _RET_ERRNO | errno.ENOSYS),  # 2  foreign ABI
        (ld, 0, 0, 0),                     # 3  nr
        (jge, 10, 0, 0x40000000),          # 4  x32 -> 15
        (jeq, 9, 0, nr_uring),             # 5  io_uring_setup -> 15
        (jeq, 4, 0, nr_socketpair),        # 6  socketpair -> 11
        (jeq, 0, 6, nr_socket),            # 7  socket -> 8, else -> 14
        (ld, 0, 0, 16),                    # 8  args[0] (domain)
        (jeq, 5, 0, _AF_UNIX),             # 9  AF_UNIX -> 15
        (ret, 0, 0, _RET_ALLOW),           # 10
        (ld, 0, 0, 24),                    # 11 args[1] (type)
        (alu_and, 0, 0, _SOCK_TYPE_MASK),  # 12 strip SOCK_CLOEXEC/SOCK_NONBLOCK
        (jeq, 1, 0, _SOCK_DGRAM),          # 13 datagram pair -> 15
        (ret, 0, 0, _RET_ALLOW),           # 14
        (ret, 0, 0, deny),                 # 15
    ]


def deny_unix_sockets() -> None:
    """Install the socket/io_uring filter (requires no_new_privs, set by restrict)."""
    program = _seccomp_program(os.uname().machine)
    filters = (_SockFilter * len(program))(*[_SockFilter(*item) for item in program])
    prog = _SockFprog(len(program), filters)
    if _syscall().prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        raise SandboxError(f"seccomp filter refused: {os.strerror(ctypes.get_errno())}")


def describe() -> dict:
    abi = abi_version()
    return {"available": abi >= MIN_ABI, "abi": abi, "filesystem": abi >= MIN_ABI, "network": abi >= 4,
            "signals": abi >= 6,
            "note": ("Kernel-enforced filesystem confinement" if abi >= MIN_ABI else
                     f"Landlock ABI {abi}: worker filesystem confinement cannot be enforced (ABI {MIN_ABI}+ needed)")}


# ------------------------------------------------------------------ launcher
def _descendants(root_pid):
    parents = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as stream:
                data = stream.read()
            parents[int(name)] = int(data[data.rindex(b")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    found, frontier = set(), {root_pid}
    while frontier:
        frontier = {pid for pid, ppid in parents.items() if ppid in frontier and pid not in found}
        found |= frontier
    return found


def _kill_descendants():
    me = os.getpid()
    for _ in range(50):
        pids = _descendants(me)
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break


def _supervise(command):
    """Run *command* as a child; reap and kill every descendant when it ends."""
    _syscall().prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    stopping = []

    def stop(signum, frame):
        stopping.append(signum)
        _kill_descendants()
        os._exit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    supervisor = os.getpid()
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        # Node dies with its supervisor (which a tool process may kill on
        # kernels without Landlock signal scoping).
        _syscall().prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != supervisor:
            os._exit(1)
        try:
            os.execvp(command[0], command)
        finally:
            os._exit(127)
    # The supervisor keeps no copy of the worker's channel to Magazine.
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1):
        os.dup2(devnull, fd)
    os.close(devnull)
    code = 1
    while True:
        try:
            pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        if pid == child:
            code = os.waitstatus_to_exitcode(status)
            break
    _kill_descendants()
    return code if code >= 0 else 128 - code


def _fail(message, code):
    print("MAGAZINE_SANDBOX_FAILED " + json.dumps({"error": message}), flush=True)
    return code


def _main(argv: list[str]) -> int:
    """``sandbox.py --policy FILE -- cmd...`` or ``sandbox.py --nest -- cmd...``."""
    # A closed standard descriptor would be taken by a pinned grant and later
    # replaced by the supervisor's dup2, silently dropping the pin.
    for fd in (0, 1, 2):
        try:
            os.fstat(fd)
        except OSError:
            null = os.open(os.devnull, os.O_RDWR)
            if null != fd:
                os.dup2(null, fd)
                os.close(null)
    if len(argv) >= 3 and argv[0] == "--nest" and argv[1] == "--":
        try:
            nest(json.loads(os.environ["MAGAZINE_TOOL_POLICY"]))
        except (KeyError, SandboxError, OSError, ValueError) as exc:
            print(f"Magazine tool sandbox could not be established: {exc}", file=sys.stderr)
            return 126
        os.environ["MAGAZINE_TOOL_DOMAIN"] = "1"
        os.execvp(argv[2], argv[2:])
    if len(argv) < 4 or argv[0] != "--policy" or argv[2] != "--":
        print("usage: sandbox.py --policy FILE -- command [args...] | --nest -- command [args...]", file=sys.stderr)
        return 64
    command = argv[3:]
    try:
        with open(argv[1], "rb") as stream:
            policy = json.loads(stream.read(4 * 1024 * 1024).decode("utf-8"))
        result = restrict(policy)
    except (SandboxError, OSError, ValueError) as exc:
        # The parent reads this exact prefix and fails the attempt closed.
        return _fail(str(exc), 70)
    os.environ["MAGAZINE_SANDBOX"] = json.dumps({"mechanism": result["mechanism"], "abi": result["abi"],
                                                 "unix_sockets": result["unix_sockets"],
                                                 "signals_scoped": result["signals_scoped"],
                                                 "write": result["write"], "tool_domain": True})
    os.environ["MAGAZINE_TOOL_POLICY"] = json.dumps(policy.get("tool", {"write": [], "protected": []}))
    os.environ["MAGAZINE_SANDBOX_PYTHON"] = sys.executable
    os.environ["MAGAZINE_SANDBOX_LAUNCHER"] = os.path.abspath(__file__)
    try:
        return _supervise(command)
    except OSError as exc:
        return _fail(f"launch failed: {exc.strerror}", 71)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
