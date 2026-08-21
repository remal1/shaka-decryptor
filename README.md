# Shaka Decryptor

A lightweight, high-performance C/C++ shared library (DLL / .so / .dylib) wrapper around [Shaka Packager](https://github.com/shaka-project/shaka-packager) dedicated exclusively to **media stream decryption**.

Designed for seamless integration into multi-language applications (**Python**, **Go**, **Node.js**, **Rust**, **C#**, etc.) with both **file-based** and **in-memory streaming (chunk/segment)** decryption, progress reporting, and granular log control.

---

## Features

- **Pure C ABI (`shaka_decryptor.h`)**: Effortlessly callable from any language via FFI (`ctypes`, `cgo`, `ffi-napi`, etc.).
- **Raw Key Decryption**: Direct KID/Key hex pair configuration (no license server dependencies required).
- **Multi-Track & Thread-Safe**: Decrypt multiple audio, video, and subtitle streams concurrently with Shaka's internal multi-threaded job manager.
- **Dual I/O Modes**:
  - **File-Based**: Directly decrypt input media files to output files.
  - **Memory/Streaming-Based (`callback://`)**: Decrypt chunks/segments on the fly as they are downloaded in memory.
- **Real-Time Progress Tracking**: Byte-accurate progress notifications for both memory callbacks and file workflows.
- **Log Interception & Console Silence**:
  - Intercept internal logs via C callback.
  - Filter log severity (`INFO`, `WARNING`, `ERROR`, `FATAL`, `NONE`).
  - Disable default console/stderr output completely for silent library embedding.

---

## Project Structure

```text
shaka-decryptor/
├── CMakeLists.txt                 # Root CMake configuration
├── include/
│   └── shaka_decryptor.h          # Public C API header
├── src/
│   └── shaka_decryptor.cc         # C API implementation
├── patches/
│   └── size_callback.patch        # Automated patch for Shaka Packager size callback
├── examples/
│   └── python/
│       └── decrypt_scenario.py    # Python ctypes integration example
└── third_party/
    └── shaka-packager/            # Git submodule (Shaka Packager core)
```

---

## Quick Start & Build

### 1. Clone with Submodules

```bash
git clone --recursive https://github.com/remal1/shaka-decryptor.git
cd shaka-decryptor
```

*(If already cloned without `--recursive`, run `git submodule update --init --recursive`)*

### 2. Build

**Windows (Visual Studio / Ninja):**
```bash
cmake -B build/
cmake --build build/ --config Release
```

**Linux / macOS:**
```bash
cmake -S . -B build/ -DCMAKE_BUILD_TYPE=Release
cmake --build build/ --parallel
```

The compiled library (`shaka_decryptor.dll`, `libshaka_decryptor.so`, or `libshaka_decryptor.dylib`) will be generated in `build/` (or `build/Release/`).

---

## Usage Example (Python)

```python
import ctypes

# Load library
shaka = ctypes.CDLL("./build/Release/shaka_decryptor.dll")

# 1. Create context
ctx = shaka.ShakaDecryptor_Create()

# 2. Add raw decryption keys
key_id = b"295EC7B2F045516B8A24939D5A299247"
key    = b"132A877545185D43BE6793B136C5BD17"
shaka.ShakaDecryptor_AddRawKey(ctx, key_id, key)

# 3. Add stream(s)
shaka.ShakaDecryptor_AddStream(ctx, b"encrypted_video.mp4", None, None, None, b"decrypted_video.mp4")

# 4. Optional: silence stderr & filter logs
shaka.ShakaDecryptor_SetConsoleLogging(0)
shaka.ShakaDecryptor_SetLogLevel(ctx, 2)  # 2 = ERROR only

# 5. Run decryption
status = shaka.ShakaDecryptor_Run(ctx)
if status != 0:
    error_msg = shaka.ShakaDecryptor_GetLastError(ctx)
    print("Error:", error_msg)

# 6. Clean up
shaka.ShakaDecryptor_Destroy(ctx)
```

See [`examples/python/decrypt_scenario.py`](examples/python/decrypt_scenario.py) for the complete reference implementation with callbacks.

---

## License

BSD 3-Clause License. See `LICENSE` for details.
