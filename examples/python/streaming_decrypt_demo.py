#!/usr/bin/env python3
"""
Real-Time In-Memory Streaming Decryption Example for Shaka Decryptor
===================================================================

Demonstrates a high-performance, memory-efficient Producer-Consumer pipeline with detailed benchmarks:
1. Producer (Downloader Thread):
   - Streams DASH fMP4 segments (init.mp4 + media segments) or progressive chunks
     over the network directly into a thread-safe FIFO buffer (StreamingQueueBuffer).
   - Measures per-segment download latency and speed.
2. Consumer (Shaka Decryptor Thread):
   - Reads chunks on the fly via C callbacks (read_cb / size_cb).
   - Decrypts segments in real-time as they arrive without keeping the full video in RAM.
   - Reports granular step timings and throughput.

Test Vector:
- Manifest: https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/Manifest_1080p_ClearKey.mpd
- KID (Base64URL): "nrQFDeRLSAKTLifXUIPiZg" -> Hex: 9eb4050de44b4802932e27d75083e266
- KEY (Base64URL): "FmY0xnWCPCNaSpRG-tUuTQ" -> Hex: 166634c675823c235a4a9446fad52e4d
"""

import argparse
import base64
import binascii
import ctypes
import os
import queue
import sys
import threading
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

WriteCallback = ctypes.CFUNCTYPE(
    ctypes.c_int64,    # return: bytes written, -1 for error
    ctypes.c_char_p,   # stream name
    ctypes.c_void_p,   # source buffer pointer
    ctypes.c_uint64,   # size in bytes
    ctypes.c_void_p    # user_data pointer
)

SizeCallback = ctypes.CFUNCTYPE(
    ctypes.c_int64,    # return: total size in bytes, or -1 if unknown
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
# Thread-Safe Streaming FIFO Queue Buffer
# ---------------------------------------------------------------------------
class StreamingQueueBuffer:
    """Thread-safe FIFO byte stream buffer between Producer (network) and Consumer (Shaka)."""

    def __init__(self, max_buffered_chunks: int = 4):
        self._q = queue.Queue(maxsize=max_buffered_chunks)
        self._current_chunk = b""
        self._offset = 0
        self._is_eof = False
        self._error = None
        self.total_bytes_pushed = 0
        self.total_bytes_consumed = 0
        self.segment_timings = []

    def push(self, data: bytes):
        """Called by Producer thread to add downloaded chunk/segment."""
        if self._error:
            raise RuntimeError(f"Stream error: {self._error}")
        self._q.put(data)
        self.total_bytes_pushed += len(data)

    def close(self):
        """Signals end of stream (EOF)."""
        self._q.put(None)

    def set_error(self, err_msg: str):
        """Signals a network or parsing failure to the consumer."""
        self._error = err_msg
        self._q.put(None)

    def read(self, size: int) -> bytes:
        """Called by Consumer callback (read_cb) to fetch next slice of data."""
        while len(self._current_chunk) - self._offset == 0:
            if self._is_eof:
                return b""
            item = self._q.get()
            if item is None:
                self._is_eof = True
                if self._error:
                    raise RuntimeError(self._error)
                return b""
            self._current_chunk = item
            self._offset = 0

        avail = len(self._current_chunk) - self._offset
        to_read = min(size, avail)
        out = self._current_chunk[self._offset : self._offset + to_read]
        self._offset += to_read
        self.total_bytes_consumed += len(out)
        return out


# ---------------------------------------------------------------------------
# Helper: Base64 / Base64URL to Hex
# ---------------------------------------------------------------------------
def b64_to_hex(b64_str: str) -> str:
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

    shaka.ShakaDecryptor_AddStreamEx.argtypes = [
        ctypes.c_void_p,   # ctx
        ctypes.c_char_p,   # name
        ReadCallback,      # read_cb
        SizeCallback,      # size_cb
        ctypes.c_void_p,   # stream_user_data
        ctypes.c_char_p,   # output_path
        WriteCallback,     # write_cb
        ctypes.c_void_p,   # write_user_data
    ]
    shaka.ShakaDecryptor_AddStreamEx.restype = ctypes.c_int

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
# Downloader Worker (Producer Thread)
# ---------------------------------------------------------------------------
def segment_downloader_thread(
    base_url: str,
    rep_id: str,
    num_segments: int,
    stream_buf: StreamingQueueBuffer,
    simulated_delay_sec: float = 0.0
):
    """Downloads init.mp4 and media segments one by one and streams them to the buffer."""
    try:
        # 1. Download init.mp4
        init_url = f"{base_url.rstrip('/')}/{rep_id}/init.mp4"
        t0 = time.perf_counter()
        with urllib.request.urlopen(init_url) as resp:
            data = resp.read()
        dur_ms = (time.perf_counter() - t0) * 1000
        speed_mb = (len(data) / (1024 * 1024)) / (dur_ms / 1000) if dur_ms > 0 else 0
        stream_buf.segment_timings.append(("Download init.mp4 (Header)", len(data), dur_ms, speed_mb))
        stream_buf.push(data)
        print(f"  [Producer] Header: init.mp4 | {len(data):,} bytes in {dur_ms:.1f} ms ({speed_mb:.2f} MB/s)", flush=True)

        # 2. Download media segments progressively
        for seg_idx in range(1, num_segments + 1):
            if simulated_delay_sec > 0:
                time.sleep(simulated_delay_sec)

            seg_url = f"{base_url.rstrip('/')}/{rep_id}/{seg_idx:04d}.m4s"
            t0 = time.perf_counter()
            with urllib.request.urlopen(seg_url) as resp:
                data = resp.read()
            dur_ms = (time.perf_counter() - t0) * 1000
            speed_mb = (len(data) / (1024 * 1024)) / (dur_ms / 1000) if dur_ms > 0 else 0
            stream_buf.segment_timings.append((f"Download segment {seg_idx:04d}.m4s", len(data), dur_ms, speed_mb))
            stream_buf.push(data)
            print(f"  [Producer] Segment #{seg_idx}: {seg_idx:04d}.m4s | {len(data):,} bytes in {dur_ms:.1f} ms ({speed_mb:.2f} MB/s)", flush=True)

        print(f"  [Producer] All {num_segments} segments downloaded. Closing stream.", flush=True)
        stream_buf.close()

    except Exception as exc:
        print(f"  [Producer ERROR] {exc}", flush=True)
        stream_buf.set_error(str(exc))


# ---------------------------------------------------------------------------
# Streaming Decryption Execution
# ---------------------------------------------------------------------------
def run_streaming_decryption(
    base_url: str,
    rep_id: str,
    num_segments: int,
    kid_b64: str,
    key_b64: str,
    output_path: str,
    memory_output: bool = False,
    simulated_delay_sec: float = 0.0,
    verbose: bool = False
):
    global_start = time.perf_counter()
    kid_hex = b64_to_hex(kid_b64)
    key_hex = b64_to_hex(key_b64)

    print("===================================================================")
    print("      REAL-TIME IN-MEMORY STREAMING DECRYPTION & BENCHMARK         ")
    print("===================================================================")
    print(f"  Representation ID : {rep_id} (1080p video)")
    print(f"  Segments to Stream: {num_segments}")
    print(f"  Simulated Latency : {simulated_delay_sec:.2f}s per segment")
    print(f"  Output Destination: {'<In-Memory Buffer>' if memory_output else output_path}")
    print(f"  KID (Hex)         : {kid_hex}")
    print(f"  KEY (Hex)         : {key_hex}")
    print("===================================================================")

    # 1. Initialize Streaming Buffer
    stream_buffer = StreamingQueueBuffer(max_buffered_chunks=3)

    # 2. Launch Producer (Downloader) Thread
    print("\n--- [1. Producer: Streaming Download Thread] ---")
    producer_thread = threading.Thread(
        target=segment_downloader_thread,
        args=(base_url, rep_id, num_segments, stream_buffer, simulated_delay_sec),
        daemon=True
    )
    producer_thread.start()

    # 3. Load Shaka Decryptor C Library
    t0 = time.perf_counter()
    shaka, dll_path = load_shaka_library()
    load_dll_ms = (time.perf_counter() - t0) * 1000

    # 4. Consumer Callback Definitions
    def py_read_cb(name, buffer_ptr, size, user_data):
        chunk = stream_buffer.read(size)
        if not chunk:
            return 0  # 0 indicates EOF
        ctypes.memmove(buffer_ptr, chunk, len(chunk))
        return len(chunk)

    def py_size_cb(name, user_data):
        return -1

    output_memory_sink = bytearray()

    def py_write_cb(name, buffer_ptr, size, user_data):
        chunk = ctypes.string_at(buffer_ptr, size)
        output_memory_sink.extend(chunk)
        return size

    def py_progress_cb(stream_name_b, bytes_processed, total_bytes, user_data):
        name = stream_name_b.decode("utf-8") if stream_name_b else "stream"
        print(f"\r  [Consumer / Shaka] Decrypted '{name}': {bytes_processed:,} bytes consumed", end="", flush=True)

    def py_log_cb(level, message_b, user_data):
        if verbose:
            msg = message_b.decode("utf-8").strip() if message_b else ""
            print(f"\n  [Shaka Log] {msg}", flush=True)

    c_read_cb     = ReadCallback(py_read_cb)
    c_size_cb     = SizeCallback(py_size_cb)
    c_write_cb    = WriteCallback(py_write_cb) if memory_output else WriteCallback(0)
    c_progress_cb = ProgressCallback(py_progress_cb)
    c_log_cb      = LogCallback(py_log_cb)

    # 5. Create Decryptor Context
    t1 = time.perf_counter()
    ctx = shaka.ShakaDecryptor_Create()
    create_ctx_ms = (time.perf_counter() - t1) * 1000
    if not ctx:
        raise RuntimeError("Failed to create ShakaDecryptor context.")

    try:
        # Add Keys
        t2 = time.perf_counter()
        ret = shaka.ShakaDecryptor_AddRawKey(ctx, kid_hex.encode("ascii"), key_hex.encode("ascii"))
        add_key_ms = (time.perf_counter() - t2) * 1000
        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"AddRawKey failed: {err.decode('utf-8') if err else 'Unknown'}")

        # Set callbacks & logging
        shaka.ShakaDecryptor_SetProgressCallback(ctx, c_progress_cb, None)
        shaka.ShakaDecryptor_SetLogCallback(ctx, c_log_cb, None)
        shaka.ShakaDecryptor_SetLogLevel(ctx, SHAKA_LOG_LEVEL_INFO if verbose else SHAKA_LOG_LEVEL_ERROR)
        shaka.ShakaDecryptor_SetConsoleLogging(1 if verbose else 0)

        stream_name = f"dash_live_rep_{rep_id}".encode("utf-8")
        out_target = b"memory_output.mp4" if memory_output else output_path.encode("utf-8")

        t3 = time.perf_counter()
        if memory_output:
            ret = shaka.ShakaDecryptor_AddStreamEx(
                ctx,
                stream_name,
                c_read_cb,
                c_size_cb,
                None,
                out_target,
                c_write_cb,
                None
            )
        else:
            ret = shaka.ShakaDecryptor_AddStream(
                ctx,
                stream_name,
                c_read_cb,
                c_size_cb,
                None,
                out_target
            )
        add_stream_ms = (time.perf_counter() - t3) * 1000

        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            raise RuntimeError(f"AddStream failed: {err.decode('utf-8') if err else 'Unknown'}")

        print("\n--- [2. Consumer: Decryption Pipeline Run] ---")
        t_run0 = time.perf_counter()
        run_res = shaka.ShakaDecryptor_Run(ctx)
        decrypt_run_ms = (time.perf_counter() - t_run0) * 1000
        print()  # Newline after progress

        if run_res != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            err_str = err.decode("utf-8") if err else "Unknown error"
            raise RuntimeError(f"Decryption failed with code {run_res}: {err_str}")

        producer_thread.join()

        t_dest0 = time.perf_counter()
        shaka.ShakaDecryptor_Destroy(ctx)
        destroy_ctx_ms = (time.perf_counter() - t_dest0) * 1000
        ctx = None

        global_elapsed_ms = (time.perf_counter() - global_start) * 1000
        total_streamed = stream_buffer.total_bytes_pushed
        pure_decrypt_speed = (total_streamed / (1024 * 1024)) / (decrypt_run_ms / 1000) if decrypt_run_ms > 0 else 0

        # Print Benchmark Table
        print("\n===================================================================")
        print("                  DETAILED STEP BENCHMARKS                         ")
        print("===================================================================")
        print(" Step Description                           Duration       Speed / Notes")
        print("-------------------------------------------------------------------")
        for desc, byte_count, dur_ms, speed_mb in stream_buffer.segment_timings:
            print(f" {desc.padEnd(38) if hasattr(desc, 'padEnd') else desc.ljust(38)} : {dur_ms:9.2f} ms | {speed_mb:6.2f} MB/s ({byte_count:,} B)")
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
        print(f" Total Data Streamed                       : {total_streamed / (1024 * 1024):.2f} MB ({total_streamed:,} bytes)")
        print(f" Pure Decryption Throughput                : {pure_decrypt_speed:.2f} MB/s ({pure_decrypt_speed * 8:.1f} Mbps)")

        if memory_output:
            with open(output_path, "wb") as f:
                f.write(output_memory_sink)
            print(f" In-Memory Output Captured & Saved         : {os.path.abspath(output_path)} ({len(output_memory_sink):,} bytes)")
        else:
            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            print(f" Decrypted Output File                     : {os.path.abspath(output_path)} ({file_size:,} bytes)")
        print("===================================================================\n")

    finally:
        if ctx:
            shaka.ShakaDecryptor_Destroy(ctx)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Real-time in-memory streaming decryption with Shaka Decryptor."
    )
    parser.add_argument(
        "--base-url",
        default="https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey",
        help="DASH stream base URL"
    )
    parser.add_argument(
        "--rep-id",
        default="5",  # 5 is 1080p video, 1 is 288p, 15 is AAC audio
        help="Representation ID (5=1080p, 4=720p, 1=288p, 15=audio)"
    )
    parser.add_argument(
        "--num-segments",
        type=int,
        default=4,
        help="Number of media segments to stream and decrypt (default: 4)"
    )
    parser.add_argument(
        "--kid",
        default="nrQFDeRLSAKTLifXUIPiZg",
        help="Key ID (Base64 / Base64URL)"
    )
    parser.add_argument(
        "--key",
        default="FmY0xnWCPCNaSpRG-tUuTQ",
        help="Key (Base64 / Base64URL)"
    )
    parser.add_argument(
        "--output",
        default="streaming_decrypted_1080p.mp4",
        help="Path for the output MP4 file"
    )
    parser.add_argument(
        "--simulated-delay",
        type=float,
        default=0.0,
        help="Simulated network latency per segment in seconds (default: 0.0s)"
    )
    parser.add_argument(
        "--memory-output",
        action="store_true",
        help="Capture decrypted output 100% in-memory via write_cb before saving"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose internal Shaka logs"
    )

    args = parser.parse_args()

    run_streaming_decryption(
        base_url=args.base_url,
        rep_id=args.rep_id,
        num_segments=args.num_segments,
        kid_b64=args.kid,
        key_b64=args.key,
        output_path=args.output,
        memory_output=args.memory_output,
        simulated_delay_sec=args.simulated_delay,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
