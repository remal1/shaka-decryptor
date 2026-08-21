import ctypes
import os
import sys

# ---------------------------------------------------------------------------
# Callback type definitions (must match shaka_decryptor.h)
# ---------------------------------------------------------------------------
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

# ShakaLogLevel enum values (matching the header)
SHAKA_LOG_LEVEL_INFO    = 0
SHAKA_LOG_LEVEL_WARNING = 1
SHAKA_LOG_LEVEL_ERROR   = 2
SHAKA_LOG_LEVEL_FATAL   = 3
SHAKA_LOG_LEVEL_NONE    = 4

# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------
def progress_handler(stream_name_b, bytes_processed, total_bytes, user_data):
    stream_name = stream_name_b.decode('utf-8') if stream_name_b else "unknown"
    if total_bytes > 0:
        percent = (bytes_processed / total_bytes) * 100
        print(f"\r  Progress [{stream_name}]: {bytes_processed}/{total_bytes} bytes ({percent:.1f}%)",
              end='', flush=True)
        if bytes_processed >= total_bytes:
            print()
    else:
        print(f"\r  Progress [{stream_name}]: {bytes_processed} bytes processed",
              end='', flush=True)

LEVEL_NAMES = ["INFO", "WARNING", "ERROR", "FATAL"]

def log_handler(level, message_b, user_data):
    message = message_b.decode('utf-8').strip() if message_b else ""
    lvl_str = LEVEL_NAMES[level] if 0 <= level < len(LEVEL_NAMES) else "UNKNOWN"
    print(f"  [SHAKA {lvl_str}] {message}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def find_dll():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    
    candidates = [
        os.path.join(repo_root, "build", "Release", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "shaka_decryptor.dll"),
        os.path.join(repo_root, "build", "libshaka_decryptor.so"),
        os.path.join(repo_root, "build", "libshaka_decryptor.dylib"),
        os.path.join(script_dir, "shaka_decryptor.dll"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

def main():
    dll_path = find_dll()
    if not dll_path:
        print("Error: shaka_decryptor library not found! Please build the project first.")
        sys.exit(1)

    print(f"Loading {dll_path}...")
    shaka = ctypes.CDLL(dll_path)

    # Function signatures
    shaka.ShakaDecryptor_Create.restype = ctypes.c_void_p
    shaka.ShakaDecryptor_Destroy.argtypes = [ctypes.c_void_p]

    shaka.ShakaDecryptor_GetLastError.argtypes = [ctypes.c_void_p]
    shaka.ShakaDecryptor_GetLastError.restype  = ctypes.c_char_p

    shaka.ShakaDecryptor_AddRawKey.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    shaka.ShakaDecryptor_AddRawKey.restype  = ctypes.c_int

    shaka.ShakaDecryptor_AddStream.argtypes = [
        ctypes.c_void_p,  # ctx
        ctypes.c_char_p,  # name / file path
        ctypes.c_void_p,  # read_cb  (NULL for file-based)
        ctypes.c_void_p,  # size_cb  (NULL for file-based)
        ctypes.c_void_p,  # stream_user_data
        ctypes.c_char_p,  # output_path
    ]
    shaka.ShakaDecryptor_AddStream.restype = ctypes.c_int

    shaka.ShakaDecryptor_SetProgressCallback.argtypes = [ctypes.c_void_p, ProgressCallback, ctypes.c_void_p]
    shaka.ShakaDecryptor_SetProgressCallback.restype  = ctypes.c_int

    shaka.ShakaDecryptor_SetLogCallback.argtypes = [ctypes.c_void_p, LogCallback, ctypes.c_void_p]
    shaka.ShakaDecryptor_SetLogCallback.restype  = ctypes.c_int

    shaka.ShakaDecryptor_SetLogLevel.argtypes = [ctypes.c_void_p, ctypes.c_int]
    shaka.ShakaDecryptor_SetLogLevel.restype  = ctypes.c_int

    shaka.ShakaDecryptor_SetConsoleLogging.argtypes = [ctypes.c_int]
    shaka.ShakaDecryptor_SetConsoleLogging.restype  = ctypes.c_int

    shaka.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]
    shaka.ShakaDecryptor_Run.restype  = ctypes.c_int

    # 1. Create Context
    print("Creating ShakaDecryptor context...")
    ctx = shaka.ShakaDecryptor_Create()
    if not ctx:
        print("Failed to create context!")
        sys.exit(1)

    try:
        # 2. Add Keys (Hex format without dashes)
        key_id = b"295EC7B2F045516B8A24939D5A299247"
        key    = b"132A877545185D43BE6793B136C5BD17"

        print("Adding key...")
        ret = shaka.ShakaDecryptor_AddRawKey(ctx, key_id, key)
        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            print(f"Error adding key: {err.decode('utf-8') if err else 'Unknown'}")
            sys.exit(1)

        # 3. Add Streams (file-based)
        input_video  = b"e:/out/video-enc.mp4"
        output_video = b"e:/out/video-dec.mp4"
        input_audio  = b"e:/out/audio-clear.mp4"
        output_audio = b"e:/out/audio-dec.mp4"

        print(f"Adding stream: {input_video.decode()}")
        shaka.ShakaDecryptor_AddStream(ctx, input_video, None, None, None, output_video)

        print(f"Adding stream: {input_audio.decode()}")
        shaka.ShakaDecryptor_AddStream(ctx, input_audio, None, None, None, output_audio)

        # 4. Set Callbacks & Logging Options
        print("Setting callbacks...")
        prog_cb = ProgressCallback(progress_handler)
        shaka.ShakaDecryptor_SetProgressCallback(ctx, prog_cb, None)

        lg_cb = LogCallback(log_handler)
        shaka.ShakaDecryptor_SetLogCallback(ctx, lg_cb, None)

        # Filter logs (only ERROR and above)
        shaka.ShakaDecryptor_SetLogLevel(ctx, SHAKA_LOG_LEVEL_ERROR)

        # Silence direct console/stderr output from internal libraries
        shaka.ShakaDecryptor_SetConsoleLogging(0)

        # 5. Run Decryption
        print("Running decryption...")
        ret = shaka.ShakaDecryptor_Run(ctx)

        if ret != 0:
            err = shaka.ShakaDecryptor_GetLastError(ctx)
            print(f"\nDecryption failed (code {ret}): {err.decode('utf-8') if err else 'Unknown error'}")
        else:
            print("\nDecryption finished successfully!")

    finally:
        print("Destroying context...")
        shaka.ShakaDecryptor_Destroy(ctx)


if __name__ == "__main__":
    main()
