"""OS networking restrictions, installed before any untrusted image parsing.

Linux uses a native-ABI seccomp filter (no Python socket replacement). macOS uses
Seatbelt through sandbox-exec before the interpreter starts. Unsupported isolation
fails closed. This is process/network isolation, not a general exploit-proof VM.
"""

import ctypes
import errno
import os
import platform
import signal
import socket
import sys


def deny_linux_network() -> str:
    machine = platform.machine().lower()
    if machine == "x86_64":
        architecture = 0xC000003E
        denied = set(range(41, 56)) | {
            101,
            288,
            299,
            307,
            310,
            311,
            321,
            425,
            426,
            427,
            438,
        }
    elif machine in {"aarch64", "arm64"}:
        architecture = 0xC00000B7
        denied = set(range(198, 213)) | {
            117,
            242,
            243,
            269,
            270,
            271,
            280,
            425,
            426,
            427,
            438,
        }
    else:
        raise RuntimeError("isolation_unavailable")

    class Instruction(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint32),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Instruction))]

    # seccomp_data.arch at offset4; syscall nr at offset0. Reject different ABIs
    # and x32 syscall numbers rather than interpreting them as native x86_64.
    rules = [
        Instruction(0x20, 0, 0, 4),
        Instruction(0x15, 1, 0, architecture),
        Instruction(0x06, 0, 0, 0x80000000),
        Instruction(0x20, 0, 0, 0),
        Instruction(0x35, 0, 1, 0x40000000),
        Instruction(0x06, 0, 0, 0x80000000),
    ]
    for number in sorted(denied):
        rules.extend(
            [
                Instruction(0x15, 0, 1, number),
                Instruction(0x06, 0, 0, 0x00050000 | errno.EPERM),
            ]
        )
    rules.append(Instruction(0x06, 0, 0, 0x7FFF0000))
    instructions = (Instruction * len(rules))(*rules)
    program = Program(len(rules), instructions)
    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getppid()
    # Kill the decoder if its worker dies, including the race before prctl.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0):
        raise RuntimeError("isolation_unavailable")
    if parent == 1 or os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise RuntimeError("isolation_unavailable")
    return "linux_seccomp_network_deny"


def install_and_check_network_deny() -> str:
    if sys.platform == "linux":
        isolation = deny_linux_network()
    elif sys.platform == "darwin":
        isolation = "macos_seatbelt_network_deny"
    else:
        raise RuntimeError("isolation_unavailable")

    # A live syscall probe must return permission denied. Refused/unreachable is
    # evidence of ordinary network access and therefore fails closed.
    for family, address in [
        (socket.AF_INET, ("127.0.0.1", 9)),
        (socket.AF_INET6, ("::1", 9)),
    ]:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.1)
                connection.connect(address)
        except OSError as error:
            if error.errno in {errno.EPERM, errno.EACCES}:
                continue
            # An OS with IPv6 disabled still must deny creation or networking for
            # IPv4. Unsupported IPv6 alone is not a path to network access.
            if family == socket.AF_INET6 and error.errno == errno.EAFNOSUPPORT:
                continue
        raise RuntimeError("isolation_unavailable")
    return isolation
