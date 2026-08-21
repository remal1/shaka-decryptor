#!/usr/bin/env python3
"""
Multi-Track & Multi-Stream Concurrent Decryption Example in Python (AsyncIO)
============================================================================

Demonstrates downloading and decrypting multiple tracks (different video resolutions
and audio languages) concurrently using Python's modern `asyncio` event loop with
connection pooling and Shaka Packager's multi-threaded C++ engine.

Features:
- Full `asyncio` networking with sliding window prefetching (Jitter Buffer).
- Non-blocking C++ execution via `await asyncio.to_thread(...)`.
- Highly accurate step timing and aggregated throughput reporting.

Tracks in this demo:
1. Video 1080p (Rep ID: 5)
2. Video 720p  (Rep ID: 4)
3. Audio EN    (Rep ID: 15)
4. Audio AU    (Rep ID: 17)

Run with:
  python examples/python/multi_stream_decrypt_demo.py
"""

import argparse
import asyncio
import base64
import binascii
import ctypes
import os
import sys
import time
import urllib.request

# Check for httpx for high-performance HTTP/2 & keep-alive connection pooling
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


def b64_to_hex(b64_str: str) -> str:
    padded = b64_str + "=" * (-len(b64_str) % 4)
    decoded_bytes = base64.urlsafe_b64decode(padded)
    return binascii.hexlify(decoded_bytes).decode("ascii")


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
        ctypes.c_void_p,   # read_cb
        ctypes.c_void_p,   # size_cb
        ctypes.c_void_p,   # stream_user_data
        ctypes.c_char_p,   # output_path
    ]
    shaka.ShakaDecryptor_AddStream.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetConsoleLogging.argtypes = [ctypes.c_int]
    shaka.ShakaDecryptor_SetConsoleLogging.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetLogLevel.argtypes = [ctypes.c_void_p, ctypes.c_int]
    shaka.ShakaDecryptor_SetLogLevel.restype = ctypes.c_int

    shaka.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]
    shaka.ShakaDecryptor_Run.restype = ctypes.c_int

    return shaka, dll_path


class AsyncTrackPrefetchQueue:
    """Sliding window prefetch queue in AsyncIO to bound memory usage per track."""

    def __init__(self, max_buffer_size: int = 5):
        self.max_buffer_size = max(1, max_buffer_size)
        self._tasks = {}

    def prefetch(self, current_idx: int, total_segments: int, fetch_coro):
        end = min(current_idx + self.max_buffer_size, total_segments + 1)
        for idx in range(current_idx, end):
            if idx not in self._tasks:
                self._tasks[idx] = asyncio.create_task(fetch_coro(idx))

    async def get_next(self, index: int) -> bytes:
        task = self._tasks.get(index)
        if not task:
            raise KeyError(f"Segment {index} not in prefetch tasks")
        data = await task
        del self._tasks[index]  # Immediately free from queue dictionary
        return data


async def download_single_track_async(
    client,
    base_url: str,
    rep_id: str,
    name: str,
    num_segments: int,
    max_buffered: int
):
    t0 = time.perf_counter()
    temp_file = f"temp_multi_py_{name}_{int(time.time()*1000)}.mp4"
    total_bytes = 0

    with open(temp_file, "wb") as f_out:
        # 1. Fetch init.mp4
        init_url = f"{base_url.rstrip('/')}/{rep_id}/init.mp4"
        if HAS_HTTPX and client:
            resp = await client.get(init_url)
            init_data = resp.content
        else:
            init_data = await asyncio.to_thread(lambda: urllib.request.urlopen(init_url).read())
        f_out.write(init_data)
        total_bytes += len(init_data)

        # 2. Fetch segments with Sliding Window Prefetch Queue
        prefetcher = AsyncTrackPrefetchQueue(max_buffered)

        async def fetch_seg(idx: int):
            seg_url = f"{base_url.rstrip('/')}/{rep_id}/{idx:04d}.m4s"
            if HAS_HTTPX and client:
                r = await client.get(seg_url)
                return r.content
            else:
                return await asyncio.to_thread(lambda: urllib.request.urlopen(seg_url).read())

        for seg_idx in range(1, num_segments + 1):
            prefetcher.prefetch(seg_idx, num_segments, fetch_seg)
            seg_data = await prefetcher.get_next(seg_idx)
            f_out.write(seg_data)
            total_bytes += len(seg_data)

    dur_ms = (time.perf_counter() - t0) * 1000
    speed_mb = (total_bytes / 1024 / 1024) / (dur_ms / 1000) if dur_ms > 0 else 0
    print(f"  [Done] {name.ljust(12)} (Rep {rep_id:>2}): {total_bytes:>10,} bytes in {dur_ms:>6.1f} ms ({speed_mb:.2f} MB/s)", flush=True)

    return {
        "name": name,
        "rep_id": rep_id,
        "input_path": temp_file,
        "bytes": total_bytes,
        "duration_ms": dur_ms,
        "speed_mb": speed_mb,
    }


async def run_multi_track_demo_async(
    base_url: str = "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey",
    num_segments: int = 50,
    max_buffered: int = 10,
    kid_b64: str = "nrQFDeRLSAKTLifXUIPiZg",
    key_b64: str = "FmY0xnWCPCNaSpRG-tUuTQ",
    verbose: bool = False
):
    global_start = time.perf_counter()
    kid_hex = b64_to_hex(kid_b64)
    key_hex = b64_to_hex(key_b64)

    tracks = [
        {"name": "Video_1080p", "type": "video", "rep_id": "5", "out_file": "py_out_video_1080p.mp4"},
        {"name": "Video_720p",  "type": "video", "rep_id": "4", "out_file": "py_out_video_720p.mp4"},
        {"name": "Audio_EN",    "type": "audio", "rep_id": "15", "out_file": "py_out_audio_en.mp4"},
        {"name": "Audio_AU",    "type": "audio", "rep_id": "17", "out_file": "py_out_audio_au.mp4"},
    ]

    print("===================================================================")
    print("  MULTI-TRACK CONCURRENT DECRYPTION DEMO (PYTHON ASYNCIO + SHAKA)  ")
    print("===================================================================")
    print(f"  Total Tracks to Process : {len(tracks)}")
    print(f"  Segments per Track      : {num_segments}")
    print(f"  Jitter Buffer per Track : {max_buffered} segments max in RAM")
    print(f"  HTTP Engine             : {'httpx (Connection Pooling & Keep-Alive)' if HAS_HTTPX else 'urllib / asyncio'}")
    print(f"  KID (Hex)               : {kid_hex}")
    print(f"  KEY (Hex)               : {key_hex}")
    print("===================================================================")

    # 1. Concurrent Network Download Phase using AsyncIO
    print("\n--- [1. Concurrent Network Download Phase (All Tracks in Parallel)] ---")
    t_down0 = time.perf_counter()

    if HAS_HTTPX:
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
        async with httpx.AsyncClient(limits=limits, timeout=15.0) as http_client:
            tasks = [
                download_single_track_async(http_client, base_url, t["rep_id"], t["name"], num_segments, max_buffered)
                for t in tracks
            ]
            download_results = await asyncio.gather(*tasks)
    else:
        tasks = [
            download_single_track_async(None, base_url, t["rep_id"], t["name"], num_segments, max_buffered)
            for t in tracks
        ]
        download_results = await asyncio.gather(*tasks)

    total_down_ms = (time.perf_counter() - t_down0) * 1000
    total_bytes = sum(r["bytes"] for r in download_results)
    print(f"\n[+] AsyncIO parallel download finished in {total_down_ms:.1f} ms (Total: {total_bytes / 1024 / 1024:.2f} MB across {len(tracks)} tracks)")

    # 2. Multi-Threaded Decryption in Shaka Decryptor
    print("\n--- [2. Concurrent Multi-Stream Decryption in C++ Engine] ---")
    print("  Registering all streams to a single Shaka Decryptor context...")
    print("  Shaka Packager will spawn dedicated C++ worker threads for each stream.")

    shaka, dll_path = load_shaka_library()
    ctx = shaka.ShakaDecryptor_Create()
    if not ctx:
        raise RuntimeError("Failed to create ShakaDecryptor context.")

    try:
        shaka.ShakaDecryptor_AddRawKey(ctx, kid_hex.encode("ascii"), key_hex.encode("ascii"))
        shaka.ShakaDecryptor_SetConsoleLogging(1 if verbose else 0)
        shaka.ShakaDecryptor_SetLogLevel(ctx, 0 if verbose else 2)

        # Register each stream to the same context
        for t in tracks:
            item = next(r for r in download_results if r["name"] == t["name"])
            ret = shaka.ShakaDecryptor_AddStream(
                ctx,
                item["input_path"].encode("utf-8"),
                None,
                None,
                None,
                t["out_file"].encode("utf-8")
            )
            if ret != 0:
                err = shaka.ShakaDecryptor_GetLastError(ctx)
                raise RuntimeError(f"AddStream failed for {t['name']}: {err}")

        # Run multi-threaded decryption asynchronously via asyncio.to_thread
        t_run0 = time.perf_counter()
        run_res = await asyncio.to_thread(shaka.ShakaDecryptor_Run, ctx)
        pure_decrypt_ms = (time.perf_counter() - t_run0) * 1000

        if run_res != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"Multi-stream decryption failed with code {run_res}: {err}")

        global_elapsed_ms = (time.perf_counter() - global_start) * 1000
        decrypt_speed = (total_bytes / 1024 / 1024) / (pure_decrypt_ms / 1000) if pure_decrypt_ms > 0 else 0

        # Clean temporary input files
        for r in download_results:
            if os.path.exists(r["input_path"]):
                os.unlink(r["input_path"])

        # 3. Print Results
        print("\n===================================================================")
        print("               MULTI-TRACK EXECUTION BENCHMARKS                    ")
        print("===================================================================")
        print(" Track Name     Type   Rep ID    Input Size    Decrypted Output File")
        print("-------------------------------------------------------------------")
        for t in tracks:
            item = next(r for r in download_results if r["name"] == t["name"])
            out_sz = os.path.getsize(t["out_file"]) if os.path.exists(t["out_file"]) else 0
            print(f" {t['name'].ljust(14)} {t['type'].ljust(6)} {t['rep_id']:>6}   {item['bytes'] / 1024 / 1024:6.2f} MB    {os.path.abspath(t['out_file'])} ({out_sz:,} B)")
        print("-------------------------------------------------------------------")
        print(f" AsyncIO Parallel Download (Wall Time)     : {total_down_ms:9.2f} ms")
        print(f" C++ Concurrent Decryption (All Tracks)    : {pure_decrypt_ms:9.2f} ms | {decrypt_speed:.2f} MB/s")
        print(f" Total End-to-End Pipeline Time            : {global_elapsed_ms:9.2f} ms ({global_elapsed_ms / 1000:.2f} s)")
        print(f" Total Processed Media Data                : {total_bytes / 1024 / 1024:.2f} MB ({total_bytes:,} bytes)")
        print(f" Jitter Buffer Setting                     : {max_buffered} segments max in RAM per track")
        print(f" Aggregated Decryption Throughput          : {decrypt_speed:.2f} MB/s ({decrypt_speed * 8:.1f} Mbps)")
        print("===================================================================\n")

    finally:
        shaka.ShakaDecryptor_Destroy(ctx)


def main():
    parser = argparse.ArgumentParser(description="Multi-track concurrent decryption benchmark with AsyncIO.")
    parser.add_argument("--num-segments", type=int, default=50, help="Number of segments per track (default: 50)")
    parser.add_argument("--max-buffered", type=int, default=10, help="Max buffered segments in RAM per track (default: 10)")
    parser.add_argument("--verbose", action="store_true", help="Enable internal Shaka logs")
    args = parser.parse_args()

    asyncio.run(run_multi_track_demo_async(
        num_segments=args.num_segments,
        max_buffered=args.max_buffered,
        verbose=args.verbose
    ))


if __name__ == "__main__":
    main()
