"""
Shaka Decryptor - High-Level Python SDK
=======================================

Provides an idiomatic, object-oriented API for media decryption,
one-shot in-memory buffer processing, multi-track packaging, and media probing.
"""

import asyncio
import base64
import ctypes
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple


class ShakaStreamMetadataStruct(ctypes.Structure):
    _fields_ = [
        ("stream_type", ctypes.c_int),          # 0=Unknown, 1=Audio, 2=Video, 3=Text
        ("codec", ctypes.c_char * 32),
        ("language", ctypes.c_char * 16),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("frame_rate", ctypes.c_double),
        ("audio_channels", ctypes.c_int),
        ("sample_rate", ctypes.c_int),
        ("duration_seconds", ctypes.c_double),
    ]


class ShakaMediaInfoStruct(ctypes.Structure):
    _fields_ = [
        ("stream_count", ctypes.c_int),
        ("streams", ShakaStreamMetadataStruct * 16),
        ("container_format", ctypes.c_char * 32),
        ("duration_seconds", ctypes.c_double),
    ]


class ShakaStatsStruct(ctypes.Structure):
    _fields_ = [
        ("total_bytes_read", ctypes.c_uint64),
        ("total_bytes_written", ctypes.c_uint64),
        ("execution_duration_ms", ctypes.c_double),
        ("throughput_mb_per_sec", ctypes.c_double),
    ]


@dataclass
class StreamMetadata:
    stream_type: str
    codec: str
    language: str
    width: int
    height: int
    frame_rate: float
    audio_channels: int
    sample_rate: int
    duration_seconds: float


@dataclass
class MediaInfo:
    container_format: str
    stream_count: int
    duration_seconds: float
    streams: List[StreamMetadata]


@dataclass
class DecryptStats:
    total_bytes_read: int
    total_bytes_written: int
    execution_duration_ms: float
    throughput_mb_s: float


def b64_to_hex(b64_str: str) -> str:
    s = b64_str.replace("-", "+").replace("_", "/")
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.b64decode(padded).hex()


def _find_library(custom_path: Optional[str] = None) -> str:
    if custom_path and os.path.exists(custom_path):
        return os.path.abspath(custom_path)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(repo_root, "build", "Release", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "libshaka_decryptor.so"),
        os.path.join(repo_root, "build", "libshaka_decryptor.dylib"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("Could not find shaka_decryptor shared library.")


_lib_instance = None


def _get_lib(custom_path: Optional[str] = None):
    global _lib_instance
    if _lib_instance is None:
        lib_path = _find_library(custom_path)
        lib = ctypes.CDLL(lib_path)

        lib.ShakaDecryptor_Create.restype = ctypes.c_void_p
        lib.ShakaDecryptor_Create.argtypes = []

        lib.ShakaDecryptor_Destroy.restype = None
        lib.ShakaDecryptor_Destroy.argtypes = [ctypes.c_void_p]

        lib.ShakaDecryptor_GetLastError.restype = ctypes.c_char_p
        lib.ShakaDecryptor_GetLastError.argtypes = [ctypes.c_void_p]

        lib.ShakaDecryptor_AddRawKey.restype = ctypes.c_int
        lib.ShakaDecryptor_AddRawKey.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]

        lib.ShakaDecryptor_AddStream.restype = ctypes.c_int
        lib.ShakaDecryptor_AddStream.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_char_p,
        ]

        lib.ShakaDecryptor_Run.restype = ctypes.c_int
        lib.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]

        lib.ShakaDecryptor_Cancel.restype = ctypes.c_int
        lib.ShakaDecryptor_Cancel.argtypes = [ctypes.c_void_p]

        lib.ShakaDecryptor_GetStats.restype = ctypes.c_int
        lib.ShakaDecryptor_GetStats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ShakaStatsStruct)]

        lib.ShakaDecryptor_ProbeMedia.restype = ctypes.c_int
        lib.ShakaDecryptor_ProbeMedia.argtypes = [ctypes.c_char_p, ctypes.POINTER(ShakaMediaInfoStruct)]

        lib.ShakaDecryptor_DecryptBuffer.restype = ctypes.c_int
        lib.ShakaDecryptor_DecryptBuffer.argtypes = [
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint64,
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)), ctypes.POINTER(ctypes.c_uint64)
        ]

        lib.ShakaDecryptor_FreeBuffer.restype = None
        lib.ShakaDecryptor_FreeBuffer.argtypes = [ctypes.POINTER(ctypes.c_uint8)]

        lib.ShakaDecryptor_SetLogLevel.restype = ctypes.c_int
        lib.ShakaDecryptor_SetLogLevel.argtypes = [ctypes.c_void_p, ctypes.c_int]

        lib.ShakaDecryptor_SetConsoleLogging.restype = ctypes.c_int
        lib.ShakaDecryptor_SetConsoleLogging.argtypes = [ctypes.c_int]

        _lib_instance = lib
    return _lib_instance


class ShakaDecryptorSession:
    def __init__(self, custom_lib_path: Optional[str] = None):
        self._lib = _get_lib(custom_lib_path)
        self._ctx = self._lib.ShakaDecryptor_Create()
        self._destroyed = False

    def add_key(self, kid_hex_or_b64: str, key_hex_or_b64: str) -> "ShakaDecryptorSession":
        kid = kid_hex_or_b64 if len(kid_hex_or_b64) == 32 else b64_to_hex(kid_hex_or_b64)
        key = key_hex_or_b64 if len(key_hex_or_b64) == 32 else b64_to_hex(key_hex_or_b64)
        self._lib.ShakaDecryptor_AddRawKey(self._ctx, kid.encode("utf-8"), key.encode("utf-8"))
        return self

    def set_log_level(self, level: int) -> "ShakaDecryptorSession":
        self._lib.ShakaDecryptor_SetLogLevel(self._ctx, level)
        return self

    def set_console_logging(self, enabled: bool) -> "ShakaDecryptorSession":
        self._lib.ShakaDecryptor_SetConsoleLogging(1 if enabled else 0)
        return self

    def add_file_stream(self, input_path: str, output_path: str) -> "ShakaDecryptorSession":
        in_abs = os.path.abspath(input_path)
        out_abs = os.path.abspath(output_path)
        self._lib.ShakaDecryptor_AddStream(
            self._ctx,
            in_abs.encode("utf-8"),
            None, None, None,
            out_abs.encode("utf-8")
        )
        return self

    def run(self) -> None:
        res = self._lib.ShakaDecryptor_Run(self._ctx)
        if res != 0:
            err = self._lib.ShakaDecryptor_GetLastError(self._ctx)
            raise RuntimeError(f"Decryption failed (code {res}): {err.decode('utf-8') if err else 'Unknown'}")

    async def run_async(self) -> None:
        await asyncio.to_thread(self.run)

    def cancel(self) -> None:
        self._lib.ShakaDecryptor_Cancel(self._ctx)

    def get_stats(self) -> DecryptStats:
        stats = ShakaStatsStruct()
        self._lib.ShakaDecryptor_GetStats(self._ctx, ctypes.byref(stats))
        return DecryptStats(
            total_bytes_read=stats.total_bytes_read,
            total_bytes_written=stats.total_bytes_written,
            execution_duration_ms=stats.execution_duration_ms,
            throughput_mb_s=stats.throughput_mb_per_sec,
        )

    def destroy(self) -> None:
        if not self._destroyed and self._ctx:
            self._lib.ShakaDecryptor_Destroy(self._ctx)
            self._destroyed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.destroy()

    @staticmethod
    def decrypt_buffer(
        data: bytes,
        kid_hex_or_b64: str,
        key_hex_or_b64: str,
        custom_lib_path: Optional[str] = None
    ) -> bytes:
        lib = _get_lib(custom_lib_path)
        kid = kid_hex_or_b64 if len(kid_hex_or_b64) == 32 else b64_to_hex(kid_hex_or_b64)
        key = key_hex_or_b64 if len(key_hex_or_b64) == 32 else b64_to_hex(key_hex_or_b64)

        in_array = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        out_ptr = ctypes.POINTER(ctypes.c_uint8)()
        out_size = ctypes.c_uint64(0)

        res = lib.ShakaDecryptor_DecryptBuffer(
            in_array,
            ctypes.c_uint64(len(data)),
            kid.encode("utf-8"),
            key.encode("utf-8"),
            ctypes.byref(out_ptr),
            ctypes.byref(out_size)
        )

        if res != 0:
            raise RuntimeError(f"One-shot buffer decryption failed with code: {res}")

        size = out_size.value
        if not out_ptr or size == 0:
            raise RuntimeError("Decryption returned empty buffer.")

        result_bytes = bytes(ctypes.string_at(out_ptr, size))
        lib.ShakaDecryptor_FreeBuffer(out_ptr)
        return result_bytes

    @staticmethod
    def probe(file_path: str, custom_lib_path: Optional[str] = None) -> MediaInfo:
        lib = _get_lib(custom_lib_path)
        info_struct = ShakaMediaInfoStruct()
        res = lib.ShakaDecryptor_ProbeMedia(
            os.path.abspath(file_path).encode("utf-8"),
            ctypes.byref(info_struct)
        )
        if res != 0:
            raise RuntimeError(f"Media probe failed with code: {res}")

        streams = []
        for i in range(info_struct.stream_count):
            s = info_struct.streams[i]
            t_str = "VIDEO" if s.stream_type == 2 else "AUDIO" if s.stream_type == 1 else "TEXT" if s.stream_type == 3 else "UNKNOWN"
            streams.append(StreamMetadata(
                stream_type=t_str,
                codec=s.codec.decode("utf-8"),
                language=s.language.decode("utf-8") or "und",
                width=s.width,
                height=s.height,
                frame_rate=s.frame_rate,
                audio_channels=s.audio_channels,
                sample_rate=s.sample_rate,
                duration_seconds=s.duration_seconds,
            ))

        return MediaInfo(
            container_format=info_struct.container_format.decode("utf-8") or "MP4/ISOBMFF",
            stream_count=info_struct.stream_count,
            duration_seconds=info_struct.duration_seconds,
            streams=streams,
        )
