"""Strict entropy validation with Pillow's already-loaded libjpeg, child-only.

Pillow deliberately suppresses libjpeg corruption warnings. LOAD_TRUNCATED_IMAGES
does not change that behavior. Read all DCT coefficients with a warning-fatal error
manager before Pillow's pixel decode. No system-library discovery or new binary.

This adapter supports libjpeg v6b's LP64 public ABI only. jpeg_CreateDecompress
validates both ABI version and exact structure size before initializing its body.
An incompatible build fails closed, and must receive a separately tested adapter.
"""

import ctypes
import os
import platform

_FatalCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_EmitCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)
_FormatCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_char))


class _MessageParameters(ctypes.Union):
    _fields_ = [("integers", ctypes.c_int * 8), ("string", ctypes.c_char * 80)]


class _ErrorManager(ctypes.Structure):
    # Public jpeg_error_mgr from libjpeg-turbo jpeglib.h (v6b ABI).
    _fields_ = [
        ("error_exit", _FatalCallback),
        ("emit_message", _EmitCallback),
        ("output_message", _FatalCallback),
        ("format_message", _FormatCallback),
        ("reset_error_mgr", _FatalCallback),
        ("message_code", ctypes.c_int),
        ("parameters", _MessageParameters),
        ("trace_level", ctypes.c_int),
        ("num_warnings", ctypes.c_long),
        ("jpeg_message_table", ctypes.c_void_p),
        ("last_jpeg_message", ctypes.c_int),
        ("addon_message_table", ctypes.c_void_p),
        ("first_addon_message", ctypes.c_int),
        ("last_addon_message", ctypes.c_int),
    ]


class _Decompressor(ctypes.Structure):
    # Only the public common prefix is accessed. The rest is opaque but sized to
    # v6b LP64 (632 bytes). Native version/size checks run before body use.
    _fields_ = [("error_manager", ctypes.POINTER(_ErrorManager)), ("opaque", ctypes.c_byte * 624)]


def _unavailable() -> None:
    raise RuntimeError("strict_decoder_unavailable")


def verify_jpeg_entropy(data: bytes) -> None:
    from PIL import _imaging, features

    if (
        platform.machine().lower() not in {"arm64", "aarch64", "x86_64"}
        or ctypes.sizeof(ctypes.c_void_p) != 8
        or ctypes.sizeof(ctypes.c_long) != 8
        or ctypes.sizeof(_Decompressor) != 632
        or ctypes.sizeof(_ErrorManager) != 168
        or features.version_codec("jpg") != "6.2"
    ):
        _unavailable()
    try:
        # Resolve symbols only through the pinned Pillow extension's dependency
        # handle. Never search PATH, ldconfig, ctypes.find_library or system dirs.
        library = ctypes.CDLL(_imaging.__file__)
        standard_error = library.jpeg_std_error
        create = library.jpeg_CreateDecompress
        memory_source = library.jpeg_mem_src
        header = library.jpeg_read_header
        coefficients = library.jpeg_read_coefficients
        finish = library.jpeg_finish_decompress
        destroy = library.jpeg_destroy_decompress
    except (OSError, AttributeError):
        _unavailable()
        return
    standard_error.argtypes = [ctypes.POINTER(_ErrorManager)]
    standard_error.restype = ctypes.POINTER(_ErrorManager)
    create.argtypes = [ctypes.POINTER(_Decompressor), ctypes.c_int, ctypes.c_size_t]
    create.restype = None
    memory_source.argtypes = [ctypes.POINTER(_Decompressor), ctypes.c_char_p, ctypes.c_ulong]
    memory_source.restype = None
    header.argtypes = [ctypes.POINTER(_Decompressor), ctypes.c_int]
    header.restype = ctypes.c_int
    coefficients.argtypes = [ctypes.POINTER(_Decompressor)]
    coefficients.restype = ctypes.c_void_p
    finish.argtypes = [ctypes.POINTER(_Decompressor)]
    finish.restype = ctypes.c_int
    destroy.argtypes = [ctypes.POINTER(_Decompressor)]
    destroy.restype = None

    initializing = True

    def stop(code: bytes) -> None:
        # Native error callbacks must never return or raise across C frames.
        # Exit only this disposable child; the parent removes its private tree.
        os.write(1, b'{"ok":false,"code":"' + code + b'"}\n')
        os._exit(0)

    @_FatalCallback
    def fatal(_context) -> None:
        stop(b"strict_decoder_unavailable" if initializing else b"invalid_image_decode")

    @_EmitCallback
    def emit(_context, level: int) -> None:
        if level < 0:
            stop(b"invalid_image_decode")

    errors = _ErrorManager()
    standard_error(ctypes.byref(errors))
    errors.error_exit = fatal
    errors.emit_message = emit
    decompressor = _Decompressor(ctypes.pointer(errors))
    create(ctypes.byref(decompressor), 62, ctypes.sizeof(decompressor))
    initializing = False
    try:
        memory_source(ctypes.byref(decompressor), data, len(data))
        if header(ctypes.byref(decompressor), 1) != 1:
            stop(b"invalid_image_decode")
        if not coefficients(ctypes.byref(decompressor)):
            stop(b"invalid_image_decode")
        if not finish(ctypes.byref(decompressor)) or errors.num_warnings:
            stop(b"invalid_image_decode")
    finally:
        destroy(ctypes.byref(decompressor))
