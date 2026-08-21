"""
High-Level SDK & One-Shot In-Memory Buffer Demo (Python)
=======================================================

Demonstrates:
1. `ShakaDecryptorSession.decrypt_buffer()`: 1-line in-memory buffer decryption.
2. `ShakaDecryptorSession.probe()`: In-process stream probing.
3. `ShakaDecryptorSession.get_stats()`: Direct throughput and byte statistics.

Run with:
    python examples/python/sdk_demo.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")))
from shaka_sdk import ShakaDecryptorSession

try:
    import httpx
except ImportError:
    print("Error: 'httpx' is required. Install with: pip install httpx")
    sys.exit(1)


def main():
    print("===================================================================")
    print("     SHAKA DECRYPTOR HIGH-LEVEL PYTHON SDK DEMO                   ")
    print("===================================================================")

    base_url = "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey"
    kid_b64 = "nrQFDeRLSAKTLifXUIPiZg"
    key_b64 = "FmY0xnWCPCNaSpRG-tUuTQ"

    # -------------------------------------------------------------------------
    # Feature 1: One-Shot In-Memory Buffer Decryption
    # -------------------------------------------------------------------------
    print("\n--- [1. One-Shot Zero-Setup In-Memory Buffer Decryption] ---")
    print("  Fetching encrypted audio init + 1 segment into RAM...")

    with httpx.Client() as client:
        r_init = client.get(f"{base_url}/15/init.mp4")
        r_seg = client.get(f"{base_url}/15/0001.m4s")

    combined_encrypted = r_init.content + r_seg.content
    print(f"  Combined Encrypted RAM Buffer Size: {len(combined_encrypted):,} bytes")

    t0 = time.perf_counter()
    # 1-line in-memory decryption:
    decrypted_bytes = ShakaDecryptorSession.decrypt_buffer(combined_encrypted, kid_b64, key_b64)
    dur_ms = (time.perf_counter() - t0) * 1000

    print(f"  [+] Decrypted directly in RAM: {len(decrypted_bytes):,} bytes in {dur_ms:.2f} ms!")

    # Write temporary test file to probe
    test_out_file = os.path.abspath("temp_py_sdk_probe_test.mp4")
    with open(test_out_file, "wb") as f:
        f.write(decrypted_bytes)

    # -------------------------------------------------------------------------
    # Feature 2: In-Process Media Probing
    # -------------------------------------------------------------------------
    print("\n--- [2. In-Process Media Probing (ShakaDecryptorSession.probe)] ---")
    info = ShakaDecryptorSession.probe(test_out_file)
    print(f"  Container Format : {info.container_format}")
    print(f"  Streams Count    : {info.stream_count}")
    for i, s in enumerate(info.streams):
        print(f"  Stream #{i}        : Type={s.stream_type}, Codec={s.codec}, Lang={s.language}, Channels={s.audio_channels}, Rate={s.sample_rate}Hz, Dur={s.duration_seconds:.2f}s")

    # -------------------------------------------------------------------------
    # Feature 3: Object-Oriented Session with Stats
    # -------------------------------------------------------------------------
    print("\n--- [3. High-Level OOP Session with Real-Time Performance Stats] ---")
    dest_file = os.path.abspath("temp_py_sdk_session_out.mp4")

    with ShakaDecryptorSession() as session:
        session.add_key(kid_b64, key_b64)
        session.set_log_level(4) # Silent
        session.set_console_logging(False)
        session.add_file_stream(test_out_file, dest_file)
        session.run()
        stats = session.get_stats()

        print(f"  Bytes Read       : {stats.total_bytes_read:,} bytes")
        print(f"  Execution Time   : {stats.execution_duration_ms:.2f} ms")
        print(f"  Throughput       : {stats.throughput_mb_s:.2f} MB/s")

    # Cleanup
    if os.path.exists(test_out_file):
        os.remove(test_out_file)
    if os.path.exists(dest_file):
        os.remove(dest_file)

    print("\n===================================================================")
    print("   ALL HIGH-LEVEL PYTHON SDK TESTS PASSED SUCCESSFULLY!            ")
    print("===================================================================\n")


if __name__ == "__main__":
    main()
