#!/usr/bin/env python3
"""
In-Memory DASH Decryption Example for Shaka Decryptor
=====================================================

This example demonstrates how to:
1. Convert Base64 / Base64URL KID and Keys to the required Hex format.
2. Download encrypted DASH segments (init.mp4 + media segments) directly into RAM.
3. Decrypt the in-memory stream using ShakaDecryptor's memory callbacks (read_cb / size_cb).
4. Save the decrypted stream to an MP4 file with precise per-step timing benchmarks.

Test Vector used:
- Manifest: https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/Manifest_1080p_ClearKey.mpd
- KID (Base64URL): "nrQFDeRLSAKTLifXUIPiZg" -> Hex: 9eb4050de44b4802932e27d75083e266
- KEY (Base64URL): "FmY0xnWCPCNaSpRG-tUuTQ" -> Hex: 166634c675823c235a4a9446fad52e4d
"""

import argparse
import base64
import binascii
import ctypes
import os
import sys
import time
import urllib.request


# ---------------------------------------------------------------------------
# Ctypes Callback Types (Matching shaka_decryptor.h)
# ---------------------------------------------------------------------------
ReadCallback = ctypes.CFUNCTYPE(
    ctypes.c_int64,    # return: bytes read, 0 for EOF, -1 for error
    ctypes.c_char_p,   # stream name
    ctypes.c_void_p,   # destination buffer pointer
    ctypes.c_uint64,   # requested byte count
    ctypes.c_void_p    # user_data pointer
)

SizeCallback = ctypes.CFUNCTYPE(
    ctypes.c_int64,    # return: total stream size in bytes, or -1 if unknown
    ctypes.c_char_p,   # stream name
    ctypes.c_void_p    # user_data pointer
)

ProgressCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_char_p,   # stream_name
    ctypes.c_uint64,   # bytes_processed
    ctypes.c_uint64,   # total_bytes (0 = unknown)
    ctypes.c_void_p    # user_data
)

LogCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int,      # level: 0=INFO, 1=WARNING, 2=ERROR, 3=FATAL
    ctypes.c_char_p,   # message
    ctypes.c_void_p    # user_data
)

SHAKA_LOG_LEVEL_INFO    = 0
SHAKA_LOG_LEVEL_WARNING = 1
SHAKA_LOG_LEVEL_ERROR   = 2
SHAKA_LOG_LEVEL_FATAL   = 3
SHAKA_LOG_LEVEL_NONE    = 4


# ---------------------------------------------------------------------------
# Helper: In-Memory Stream Buffer Wrapper
# ---------------------------------------------------------------------------
class InMemoryStream:
    """Manages an in-memory byte buffer and provides read/size callbacks for C ABI."""

    def __init__(self, data: bytes, name: str = "in_memory_stream"):
        self.data = data
        self.name = name
        self.offset = 0

    def read(self, size: int) -> bytes:
        if self.offset >= len(self.data):
            return b""
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def total_size(self) -> int:
        return len(self.data)

    def reset(self):
        self.offset = 0


# ---------------------------------------------------------------------------
# Helper: Base64 / Base64URL to Hex converter
# ---------------------------------------------------------------------------
def b64_to_hex(b64_str: str) -> str:
    """Converts a standard base64 or URL-safe base64 string to a hex string."""
    padded = b64_str + "=" * (-len(b64_str) % 4)
    decoded_bytes = base64.urlsafe_b64decode(padded)
    return binascii.hexlify(decoded_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# DLL Loader & Bindings
# ---------------------------------------------------------------------------
def load_shaka_library(custom_dll_path: str = None):
    candidates = []
    if custom_dll_path:
        candidates.append(custom_dll_path)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))

    candidates.extend([
        os.path.join(repo_root, "build", "Release", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "libshaka_decryptor.so"),
        os.path.join(repo_root, "build", "libshaka_decryptor.dylib"),
    ])

    dll_path = None
    for c in candidates:
        if os.path.exists(c):
            dll_path = c
            break

    if not dll_path:
        raise FileNotFoundError("Could not find shaka_decryptor library. Please build the project first.")

    shaka = ctypes.CDLL(dll_path)

    shaka.ShakaDecryptor_Create.restype = ctypes.c_void_p
    shaka.ShakaDecryptor_Destroy.argtypes = [ctypes.c_void_p]

    shaka.ShakaDecryptor_GetLastError.argtypes = [ctypes.c_void_p]
    shaka.ShakaDecryptor_GetLastError.restype = ctypes.c_char_p

    shaka.ShakaDecryptor_AddRawKey.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    shaka.ShakaDecryptor_AddRawKey.restype = ctypes.c_int

    shaka.ShakaDecryptor_AddStream.argtypes = [
        ctypes.c_void_p,   # ctx
        ctypes.c_char_p,   # name
        ReadCallback,      # read_cb
        SizeCallback,      # size_cb
        ctypes.c_void_p,   # stream_user_data
        ctypes.c_char_p,   # output_path
    ]
    shaka.ShakaDecryptor_AddStream.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetProgressCallback.argtypes = [ctypes.c_void_p, ProgressCallback, ctypes.c_void_p]
    shaka.ShakaDecryptor_SetProgressCallback.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetLogCallback.argtypes = [ctypes.c_void_p, LogCallback, ctypes.c_void_p]
    shaka.ShakaDecryptor_SetLogCallback.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetLogLevel.argtypes = [ctypes.c_void_p, ctypes.c_int]
    shaka.ShakaDecryptor_SetLogLevel.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetConsoleLogging.argtypes = [ctypes.c_int]
    shaka.ShakaDecryptor_SetConsoleLogging.restype = ctypes.c_int

    shaka.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]
    shaka.ShakaDecryptor_Run.restype = ctypes.c_int

    return shaka, dll_path


# ---------------------------------------------------------------------------
# Main Demo Routine
# ---------------------------------------------------------------------------
def decrypt_in_memory(
    base_url: str,
    rep_id: str,
    num_segments: int,
    kid_b64: str,
    key_b64: str,
    output_path: str,
    dll_path: str = None,
    verbose: bool = False
):
    global_start = time.perf_counter()
    kid_hex = b64_to_hex(kid_b64)
    key_hex = b64_to_hex(key_b64)

    print("===================================================================")
    print("      IN-MEMORY BUFFER DECRYPTION & BENCHMARK (PYTHON)             ")
    print("===================================================================")
    print(f"  Representation ID : {rep_id}")
    print(f"  Segments to Load  : {num_segments}")
    print(f"  Output Path       : {output_path}")
    print(f"  KID (Hex)         : {kid_hex}")
    print(f"  KEY (Hex)         : {key_hex}")
    print("===================================================================")

    step_benchmarks = []

    # 1. Download DASH initialization and media segments into RAM
    print("\n--- [1. Downloading Segments to RAM] ---")
    memory_buffer = bytearray()

    # Download init.mp4
    init_url = f"{base_url.rstrip('/')}/{rep_id}/init.mp4"
    t0 = time.perf_counter()
    with urllib.request.urlopen(init_url) as response:
        init_data = response.read()
    init_ms = (time.perf_counter() - t0) * 1000
    init_speed = (len(init_data) / 1024 / 1024) / (init_ms / 1000) if init_ms > 0 else 0
    memory_buffer.extend(init_data)
    step_benchmarks.append(("Download init.mp4 (Header)", len(init_data), init_ms, init_speed))
    print(f"  [Header] init.mp4 : {len(init_data):>9,} bytes in {init_ms:>6.1f} ms ({init_speed:.2f} MB/s)")

    # Download media segments
    for seg_idx in range(1, num_segments + 1):
        seg_url = f"{base_url.rstrip('/')}/{rep_id}/{seg_idx:04d}.m4s"
        t0 = time.perf_counter()
        with urllib.request.urlopen(seg_url) as response:
            seg_data = response.read()
        seg_ms = (time.perf_counter() - t0) * 1000
        seg_speed = (len(seg_data) / 1024 / 1024) / (seg_ms / 1000) if seg_ms > 0 else 0
        memory_buffer.extend(seg_data)
        step_benchmarks.append((f"Download segment {seg_idx:04d}.m4s", len(seg_data), seg_ms, seg_speed))
        print(f"  [Seg #{seg_idx}] {seg_idx:04d}.m4s : {len(seg_data):>9,} bytes in {seg_ms:>6.1f} ms ({seg_speed:.2f} MB/s)")

    total_in_memory_bytes = len(memory_buffer)
    print(f"\n[+] Total in-memory data ready: {total_in_memory_bytes:,} bytes in RAM")

    # 2. Create InMemoryStream instance
    mem_stream = InMemoryStream(bytes(memory_buffer), name=f"dash_rep_{rep_id}")

    def py_read_cb(name, buffer_ptr, size, user_data):
        chunk = mem_stream.read(size)
        if not chunk:
            return 0  # 0 indicates EOF to the demuxer
        ctypes.memmove(buffer_ptr, chunk, len(chunk))
        return len(chunk)

    def py_size_cb(name, user_data):
        return mem_stream.total_size()

    def py_progress_cb(stream_name_b, bytes_processed, total_bytes, user_data):
        name = stream_name_b.decode("utf-8") if stream_name_b else "stream"
        if total_bytes > 0:
            pct = (bytes_processed / total_bytes) * 100
            print(f"\r  [Progress] {name}: {bytes_processed:,}/{total_bytes:,} bytes ({pct:.1f}%)", end="", flush=True)
        else:
            print(f"\r  [Progress] {name}: {bytes_processed:,} bytes processed", end="", flush=True)

    def py_log_cb(level, message_b, user_data):
        if verbose:
            msg = message_b.decode("utf-8").strip() if message_b else ""
            print(f"  [Shaka Log] {msg}")

    c_read_cb = ReadCallback(py_read_cb)
    c_size_cb = SizeCallback(py_size_cb)
    c_prog_cb = ProgressCallback(py_progress_cb)
    c_log_cb  = LogCallback(py_log_cb)

    # 3. Load Shaka Decryptor C library
    t0 = time.perf_counter()
    shaka, loaded_dll_path = load_shaka_library(dll_path)
    load_dll_ms = (time.perf_counter() - t0) * 1000

    # 4. Initialize Decryptor Context
    t1 = time.perf_counter()
    ctx = shaka.ShakaDecryptor_Create()
    create_ctx_ms = (time.perf_counter() - t1) * 1000
    if not ctx:
        raise RuntimeError("Failed to create ShakaDecryptor context.")

    try:
        # Add Decryption Key
        t2 = time.perf_counter()
        ret = shaka.ShakaDecryptor_AddRawKey(
            ctx,
            kid_hex.encode("ascii"),
            key_hex.encode("ascii")
        )
        add_key_ms = (time.perf_counter() - t2) * 1000
        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"Failed to add raw key: {err.decode('utf-8') if err else 'Unknown'}")

        # Set callbacks and logging
        shaka.ShakaDecryptor_SetProgressCallback(ctx, c_prog_cb, None)
        shaka.ShakaDecryptor_SetLogCallback(ctx, c_log_cb, None)
        shaka.ShakaDecryptor_SetLogLevel(ctx, SHAKA_LOG_LEVEL_INFO if verbose else SHAKA_LOG_LEVEL_ERROR)
        shaka.ShakaDecryptor_SetConsoleLogging(1 if verbose else 0)

        # Register in-memory stream
        stream_name = mem_stream.name.encode("utf-8")
        out_path_b = output_path.encode("utf-8")

        t3 = time.perf_counter()
        ret = shaka.ShakaDecryptor_AddStream(
            ctx,
            stream_name,
            c_read_cb,
            c_size_cb,
            None,
            out_path_b
        )
        add_stream_ms = (time.perf_counter() - t3) * 1000
        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"AddStream failed: {err.decode('utf-8') if err else 'Unknown'}")

        # Execute decryption
        print("\n--- [2. Running Decryption Pipeline] ---")
        t_run0 = time.perf_counter()
        run_res = shaka.ShakaDecryptor_Run(ctx)
        decrypt_run_ms = (time.perf_counter() - t_run0) * 1000
        print()  # newline after progress

        if run_res != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            err_str = err.decode("utf-8") if err else "Unknown error"
            raise RuntimeError(f"Decryption failed with code {run_res}: {err_str}")

        t_dest0 = time.perf_counter()
        shaka.ShakaDecryptor_Destroy(ctx)
        destroy_ctx_ms = (time.perf_counter() - t_dest0) * 1000
        ctx = None

        global_elapsed_ms = (time.perf_counter() - global_start) * 1000
        pure_decrypt_speed = (total_in_memory_bytes / 1024 / 1024) / (decrypt_run_ms / 1000) if decrypt_run_ms > 0 else 0
        out_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

        # Print Benchmark Table
        print("\n===================================================================")
        print("                  DETAILED STEP BENCHMARKS                         ")
        print("===================================================================")
        print(" Step Description                           Duration       Speed / Notes")
        print("-------------------------------------------------------------------")
        for desc, byte_count, dur_ms, speed_mb in step_benchmarks:
            print(f" {desc.ljust(38)} : {dur_ms:9.2f} ms | {speed_mb:6.2f} MB/s ({byte_count:,} B)")
        print("-------------------------------------------------------------------")
        print(f" C++ Native Execution Breakdown:")
        print(f"   - Load DLL & Bindings                   : {load_dll_ms:9.2f} ms")
        print(f"   - ShakaDecryptor_Create                 : {create_ctx_ms:9.2f} ms")
        print(f"   - ShakaDecryptor_AddRawKey              : {add_key_ms:9.2f} ms")
        print(f"   - ShakaDecryptor_AddStream              : {add_stream_ms:9.2f} ms")
        print(f"   - ShakaDecryptor_Run (Pure Decrypt+Mux) : {decrypt_run_ms:9.2f} ms | {pure_decrypt_speed:.2f} MB/s")
        print(f"   - ShakaDecryptor_Destroy                : {destroy_ctx_ms:9.2f} ms")
        print("-------------------------------------------------------------------")
        print(f" Total Pipeline Time (End-to-End)          : {global_elapsed_ms:9.2f} ms ({global_elapsed_ms / 1000:.2f} s)")
        print(f" Total Data Processed                      : {total_in_memory_bytes / 1024 / 1024:.2f} MB ({total_in_memory_bytes:,} bytes)")
        print(f" Pure Decryption Throughput                : {pure_decrypt_speed:.2f} MB/s ({pure_decrypt_speed * 8:.1f} Mbps)")
        print(f" Decrypted Output File                     : {os.path.abspath(output_path)} ({out_size:,} bytes)")
        print("===================================================================\n")

    finally:
        if ctx:
            shaka.ShakaDecryptor_Destroy(ctx)


# ---------------------------------------------------------------------------
# CLI Argument Parser
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="In-memory DASH decryption demonstration with Shaka Decryptor.")
    parser.add_argument(
        "--base-url",
        default="https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey",
        help="Base URL of DASH stream"
    )
    parser.add_argument(
        "--rep-id",
        default="5",  # Representation 5 is 1080p (1920x1080), 1 is 288p (512x288)
        help="Representation ID (e.g. 1 for 288p, 5 for 1080p video, 15 for audio)"
    )
    parser.add_argument(
        "--num-segments",
        type=int,
        default=3,
        help="Number of media segments to download and decrypt (default: 3)"
    )
    parser.add_argument(
        "--kid",
        default="nrQFDeRLSAKTLifXUIPiZg",
        help="Key ID (Base64 / Base64URL format)"
    )
    parser.add_argument(
        "--key",
        default="FmY0xnWCPCNaSpRG-tUuTQ",
        help="Key (Base64 / Base64URL format)"
    )
    parser.add_argument(
        "--output",
        default="decrypted_1080p_sample.mp4",
        help="Output file path for the decrypted video"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed Shaka internal logging"
    )

    args = parser.parse_args()

    decrypt_in_memory(
        base_url=args.base_url,
        rep_id=args.rep_id,
        num_segments=args.num_segments,
        kid_b64=args.kid,
        key_b64=args.key,
        output_path=args.output,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
