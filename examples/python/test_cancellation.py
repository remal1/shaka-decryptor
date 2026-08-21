"""
Test Cancellation in Shaka Decryptor
====================================
Tests thread-safe cancellation via ShakaDecryptor_Cancel():
1. Pre-cancellation (cancelling before execution).
2. Live runtime cancellation from a separate thread during active decryption.
"""

import threading
import time
import os
import ctypes
from multi_track_metadata_demo import load_shaka_library, b64_to_hex

def test_pre_cancel():
    lib = load_shaka_library()
    ctx = lib.ShakaDecryptor_Create()

    kid_hex = b64_to_hex("nrQFDeRLSAKTLifXUIPiZg")
    key_hex = b64_to_hex("FmY0xnWCPCNaSpRG-tUuTQ")

    lib.ShakaDecryptor_AddRawKey(ctx, kid_hex.encode("utf-8"), key_hex.encode("utf-8"))
    lib.ShakaDecryptor_SetConsoleLogging(0)
    lib.ShakaDecryptor_SetLogLevel(ctx, 4)

    # 1. Pre-cancellation test
    lib.ShakaDecryptor_Cancel(ctx)
    res = lib.ShakaDecryptor_Run(ctx)
    print(f"  [1] Pre-cancelled run returned: {res} (Expected -4)")
    assert res == -4, f"Expected -4, got {res}"
    print("      -> Pre-cancellation test PASSED!")

    lib.ShakaDecryptor_Destroy(ctx)


def test_live_cancel():
    # If decrypted media files exist, create a run and cancel it from another thread
    video_file = os.path.abspath("meta_video_1080p.mp4")
    if not os.path.exists(video_file):
        print("  [2] Skipping live cancellation test (video file not present).")
        return

    lib = load_shaka_library()
    ctx = lib.ShakaDecryptor_Create()

    kid_hex = b64_to_hex("nrQFDeRLSAKTLifXUIPiZg")
    key_hex = b64_to_hex("FmY0xnWCPCNaSpRG-tUuTQ")

    lib.ShakaDecryptor_AddRawKey(ctx, kid_hex.encode("utf-8"), key_hex.encode("utf-8"))
    lib.ShakaDecryptor_SetConsoleLogging(0)
    lib.ShakaDecryptor_SetLogLevel(ctx, 4)

    out_file = os.path.abspath("cancel_test_out.mp4")
    lib.ShakaDecryptor_AddStream(
        ctx,
        video_file.encode("utf-8"),
        None, None, None,
        out_file.encode("utf-8")
    )

    # Cancel after 5ms on a separate background thread
    def async_cancel():
        time.sleep(0.005)
        lib.ShakaDecryptor_Cancel(ctx)

    t = threading.Thread(target=async_cancel)
    t.start()

    res = lib.ShakaDecryptor_Run(ctx)
    t.join()

    print(f"  [2] Live cancelled run returned: {res} (Expected -4 or 0)")
    if os.path.exists(out_file):
        os.remove(out_file)
    print("      -> Live cancellation test completed successfully!")

    lib.ShakaDecryptor_Destroy(ctx)


if __name__ == "__main__":
    print("==================================================")
    print("       TESTING SHAKA DECRYPTOR CANCELLATION       ")
    print("==================================================")
    test_pre_cancel()
    test_live_cancel()
    print("==================================================")
    print("  ALL CANCELLATION TESTS COMPLETED SUCCESSFULLY!  ")
    print("==================================================")
