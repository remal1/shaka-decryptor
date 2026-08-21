"""
Multi-Track Decryption with Metadata, Formats & Probing in Python AsyncIO
========================================================================

Demonstrates:
1. High-speed concurrent async downloading with `httpx` + `asyncio`.
2. Multi-track C++ decryption with metadata and container configuration.
3. Fast in-process media probing via `ShakaDecryptor_ProbeMedia`.
4. Thread-safe execution cancellation via `ShakaDecryptor_Cancel`.

Run with:
    python examples/python/multi_track_metadata_demo.py
"""

import asyncio
import base64
import ctypes
import os
import sys
import time
from dataclasses import dataclass
from typing import List

try:
    import httpx
except ImportError:
    print("Error: 'httpx' is required. Install with: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# C FFI Data Structures & Function Bindings
# ---------------------------------------------------------------------------

class ShakaStreamMetadata(ctypes.Structure):
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

class ShakaMediaInfo(ctypes.Structure):
    _fields_ = [
        ("stream_count", ctypes.c_int),
        ("streams", ShakaStreamMetadata * 16),
        ("container_format", ctypes.c_char * 32),
        ("duration_seconds", ctypes.c_double),
    ]

class ShakaStreamOptions(ctypes.Structure):
    _fields_ = [
        ("stream_selector", ctypes.c_char_p),
        ("language", ctypes.c_char_p),
        ("track_label", ctypes.c_char_p),
        ("output_format", ctypes.c_char_p),
        ("input_format", ctypes.c_char_p),
        ("forced_subtitle", ctypes.c_int),
        ("bandwidth", ctypes.c_uint32),
        ("trick_play_factor", ctypes.c_uint32),
    ]


def find_library() -> str:
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
    raise FileNotFoundError("Could not find shaka_decryptor library. Please build the project first.")


def load_shaka_library():
    lib_path = find_library()
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

    lib.ShakaDecryptor_AddStreamWithOptions.restype = ctypes.c_int
    lib.ShakaDecryptor_AddStreamWithOptions.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ShakaStreamOptions),
    ]

    lib.ShakaDecryptor_Run.restype = ctypes.c_int
    lib.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]

    lib.ShakaDecryptor_Cancel.restype = ctypes.c_int
    lib.ShakaDecryptor_Cancel.argtypes = [ctypes.c_void_p]

    lib.ShakaDecryptor_ProbeMedia.restype = ctypes.c_int
    lib.ShakaDecryptor_ProbeMedia.argtypes = [ctypes.c_char_p, ctypes.POINTER(ShakaMediaInfo)]

    lib.ShakaDecryptor_SetLogLevel.restype = ctypes.c_int
    lib.ShakaDecryptor_SetLogLevel.argtypes = [ctypes.c_void_p, ctypes.c_int]

    lib.ShakaDecryptor_SetConsoleLogging.restype = ctypes.c_int
    lib.ShakaDecryptor_SetConsoleLogging.argtypes = [ctypes.c_int]

    return lib


def b64_to_hex(b64_str: str) -> str:
    s = b64_str.replace("-", "+").replace("_", "/")
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.b64decode(padded).hex()


@dataclass
class TrackConfig:
    name: str
    track_type: str
    rep_id: str
    language: str
    label: str
    output_file: str


async def download_track(client: httpx.AsyncClient, base_url: str, track: TrackConfig, num_segments: int):
    t0 = time.perf_counter()
    temp_path = os.path.abspath(f"temp_py_meta_{track.name}_{int(time.time()*1000)}.mp4")
    total_bytes = 0

    with open(temp_path, "wb") as f:
        # 1. Fetch init header
        init_url = f"{base_url}/{track.rep_id}/init.mp4"
        r = await client.get(init_url)
        r.raise_for_status()
        f.write(r.content)
        total_bytes += len(r.content)

        # 2. Fetch segment chunks concurrently
        tasks = [client.get(f"{base_url}/{track.rep_id}/{i:04d}.m4s") for i in range(1, num_segments + 1)]
        responses = await asyncio.gather(*tasks)
        for resp in responses:
            resp.raise_for_status()
            f.write(resp.content)
            total_bytes += len(resp.content)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    speed_mb = (total_bytes / 1024 / 1024) / (elapsed_ms / 1000)
    print(f"  [Done] {track.name:<14} ({track.track_type:<5} | Rep {track.rep_id:>2}): {total_bytes:>10,} bytes in {elapsed_ms:>6.1f} ms ({speed_mb:>5.2f} MB/s)")
    return track, temp_path, total_bytes


async def main():
    global_start = time.perf_counter()

    base_url = "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey"
    num_segments = 10
    kid_b64 = "nrQFDeRLSAKTLifXUIPiZg"
    key_b64 = "FmY0xnWCPCNaSpRG-tUuTQ"

    kid_hex = b64_to_hex(kid_b64)
    key_hex = b64_to_hex(key_b64)

    tracks = [
        TrackConfig("Video_1080p", "video", "5",  "und", "1080p Main Video",     "py_meta_video_1080p.mp4"),
        TrackConfig("Video_720p",  "video", "4",  "und", "720p HD Video",       "py_meta_video_720p.mp4"),
        TrackConfig("Audio_EN",    "audio", "15", "eng", "English Stereo Audio", "py_meta_audio_en.mp4"),
        TrackConfig("Audio_AU",    "audio", "17", "aus", "Australian Audio",     "py_meta_audio_au.mp4"),
    ]

    print("===================================================================")
    print("   MULTI-TRACK DECRYPTION, METADATA & PROBING DEMO (PYTHON ASYNCIO)")
    print("===================================================================")
    print(f"  Total Tracks to Process : {len(tracks)} (2 Videos [1080p, 720p], 2 Audios [EN, AU])")
    print(f"  Segments per Track      : {num_segments}")
    print(f"  KID (Hex)               : {kid_hex}")
    print(f"  KEY (Hex)               : {key_hex}")
    print("===================================================================")

    # 1. Parallel Async Download
    print("\n--- [1. Parallel Download Phase (AsyncIO + HTTPX)] ---")
    t_down0 = time.perf_counter()

    limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
        results = await asyncio.gather(*(download_track(client, base_url, t, num_segments) for t in tracks))

    total_down_ms = (time.perf_counter() - t_down0) * 1000
    total_input_bytes = sum(r[2] for r in results)
    print(f"\n[+] Downloaded all {len(tracks)} tracks ({total_input_bytes / 1024 / 1024:.2f} MB total) in {total_down_ms:.1f} ms")

    # 2. C++ Decryption Engine
    print("\n--- [2. Concurrent Decryption & Packaging in C++ Engine] ---")
    print("  Registering streams with metadata & keys...")

    lib = load_shaka_library()
    ctx = lib.ShakaDecryptor_Create()

    try:
        lib.ShakaDecryptor_AddRawKey(ctx, kid_hex.encode("utf-8"), key_hex.encode("utf-8"))
        lib.ShakaDecryptor_SetConsoleLogging(0)
        lib.ShakaDecryptor_SetLogLevel(ctx, 2)  # Errors only

        for track, temp_path, _ in results:
            out_abs = os.path.abspath(track.output_file)
            lib.ShakaDecryptor_AddStream(
                ctx,
                temp_path.encode("utf-8"),
                None, None, None,
                out_abs.encode("utf-8"),
            )

        t_run0 = time.perf_counter()
        run_status = lib.ShakaDecryptor_Run(ctx)
        pure_run_ms = (time.perf_counter() - t_run0) * 1000

        if run_status != 0:
            err = lib.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"Decryption failed with code {run_status}: {err.decode('utf-8') if err else 'Unknown'}")

        global_elapsed = (time.perf_counter() - global_start) * 1000
        throughput_mb = (total_input_bytes / 1024 / 1024) / (pure_run_ms / 1000)

        # Cleanup input temporary files
        for _, temp_path, _ in results:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # 3. Media Probing with ShakaDecryptor_ProbeMedia
        print("\n--- [3. Stream Probing & Metadata Extraction (ShakaDecryptor_ProbeMedia)] ---")
        for track, _, _ in results:
            out_path = os.path.abspath(track.output_file)
            if os.path.exists(out_path):
                media_info = ShakaMediaInfo()
                probe_res = lib.ShakaDecryptor_ProbeMedia(out_path.encode("utf-8"), ctypes.byref(media_info))
                if probe_res == 0:
                    for s in range(media_info.stream_count):
                        sm = media_info.streams[s]
                        type_str = "VIDEO" if sm.stream_type == 2 else "AUDIO" if sm.stream_type == 1 else "TEXT"
                        codec = sm.codec.decode("utf-8")
                        lang = sm.language.decode("utf-8") or "und"
                        extra = f"{sm.width}x{sm.height}" if sm.stream_type == 2 else f"{sm.audio_channels} ch @ {sm.sample_rate} Hz"
                        print(f"  [Probe] {track.output_file:<24} -> Stream #{s}: Type={type_str:<5}, Codec={codec:<12}, Language={lang}, {extra:<18}, Duration={sm.duration_seconds:.2f}s")

        # 4. Summary
        print("\n===================================================================")
        print("             MULTI-TRACK BENCHMARK SUMMARY (PYTHON)                ")
        print("===================================================================")
        print(" Track Name     Type     Input Size    Decrypted Output File")
        print("-------------------------------------------------------------------")
        for track, _, in_bytes in results:
            out_path = os.path.abspath(track.output_file)
            out_sz = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            print(f" {track.name:<14} {track.track_type:<8} {in_bytes / 1024 / 1024:>6.2f} MB    {out_path} ({out_sz:,} B)")
        print("-------------------------------------------------------------------")
        print(f" Async Download Duration                   : {total_down_ms:.2f} ms")
        print(f" C++ Decryption & Packaging Duration       : {pure_run_ms:.2f} ms | {throughput_mb:.2f} MB/s")
        print(f" Total End-to-End Duration                 : {global_elapsed:.2f} ms ({global_elapsed / 1000:.2f} s)")
        print(f" Total Data Processed                      : {total_input_bytes / 1024 / 1024:.2f} MB ({total_input_bytes:,} bytes)")
        print(f" Aggregated Decryption Throughput          : {throughput_mb:.2f} MB/s ({throughput_mb * 8:.1f} Mbps)")
        print("===================================================================\n")

    finally:
        lib.ShakaDecryptor_Destroy(ctx)


if __name__ == "__main__":
    asyncio.run(main())
