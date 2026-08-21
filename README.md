# Shaka Decryptor

A lightweight, high-performance C/C++ shared library (`.dll` / `.so` / `.dylib`) wrapper around [Shaka Packager](https://github.com/shaka-project/shaka-packager) dedicated exclusively to **high-throughput media stream decryption, metadata tagging, container repacking, and probing**.

Designed for seamless integration into multi-language applications (**Bun JS**, **Node.js**, **Python**, **Go**, **Rust**, **C#**, **C/C++**, etc.) with both **file-based** and **in-memory / streaming (chunk/segment)** decryption, one-shot RAM buffer decryption, multi-track concurrency, real-time progress reporting, granular log control, stream probing, and thread-safe cancellation.

---

## Key Features

- **Pure C ABI (`shaka_decryptor.h`)**: Effortlessly callable from any runtime via FFI (`bun:ffi`, Node.js `koffi`/`ffi-napi`, Python `ctypes`, Go `cgo`, Rust `bindgen`, C# `P/Invoke`).
- **One-Shot Zero-Setup In-Memory Decryption (`ShakaDecryptor_DecryptBuffer`)**: 1-line RAM buffer decryption for single files or individual fMP4 segments.
- **Multi-Track & Multi-Threaded**: Decrypt multiple video resolutions, audio languages, and subtitle tracks concurrently on all CPU cores in a single pass.
- **Granular Stream Metadata**: Set track languages (ISO-639-2 tags e.g. `hun`, `eng`), stream selector (`video`, `audio`, `text`), track titles/labels, and format converters (`mp4`, `webm`, `ts`).
- **In-Process Media Probing (`ShakaDecryptor_ProbeMedia`)**: Instantly inspect container format, video resolution, framerate, audio channels, sampling frequency, codecs, and durations without launching external processes.
- **Thread-Safe Cancellation (`ShakaDecryptor_Cancel`)**: Cancel active packaging/decryption jobs instantly from any thread or timer.
- **Performance & Statistics API (`ShakaDecryptor_GetStats`)**: Direct metrics for bytes read/written, duration, and calculated MB/s throughput.
- **Dual I/O Paradigms**:
  - **File-Based**: Direct filesystem input/output.
  - **Memory & Streaming Callbacks (`callback://`)**: Pipe live downloaded chunks (DASH/HLS/fMP4) directly from RAM into the decryptor and capture decrypted output in RAM or write to disk.
- **Bounded Jitter Buffer Support**: Stream massive video files (50+ GB) with bounded RAM usage (~1.5–5 MB).
- **Raw Key Configuration**: Direct KID/Key hex pair registration (no license server dependencies).
- **Byte-Accurate Progress Reporting**: Real-time progress notifications for both memory callbacks and file workflows.
- **Log Interception & Console Silence**:
  - Intercept internal logs via C callback.
  - Filter log severity (`INFO`, `WARNING`, `ERROR`, `FATAL`, `NONE`).
  - Disable default console/stderr output completely for silent library embedding.

---

## Project Structure & SDKs

```text
shaka-decryptor/
├── CMakeLists.txt                      # Root CMake build configuration
├── include/
│   └── shaka_decryptor.h               # Public C API header
├── src/
│   └── shaka_decryptor.cc              # C API implementation
├── patches/
│   └── shaka_packager.patch            # Unified patch for Shaka Packager core
├── sdk/
│   ├── bun/                            # High-level TypeScript / Bun SDK
│   │   └── shaka_sdk.ts                # ShakaDecryptorSession, decryptBuffer, probe
│   └── python/                         # High-level Python SDK
│       └── shaka_sdk.py                # ShakaDecryptorSession, decrypt_buffer, probe
├── examples/
│   ├── bun/                            # Bun JS / TypeScript Examples (bun:ffi)
│   │   ├── shaka_bindings.ts           # Shared TypeScript FFI bindings module
│   │   ├── decrypt_worker.ts           # Dedicated Worker thread for non-blocking decryption
│   │   ├── decrypt_scenario.ts         # File-based scenario in Worker thread
│   │   ├── in_memory_decrypt_demo.ts   # In-memory RAM buffer decryption
│   │   ├── streaming_decrypt_demo.ts   # Real-time streaming with Jitter Buffer (Sliding Window)
│   │   ├── multi_stream_decrypt_demo.ts # Multi-track (video+audio) concurrent decryption
│   │   ├── multi_track_metadata_demo.ts # Metadata, formats & media probing demo
│   │   └── sdk_demo.ts                 # High-level TypeScript SDK demo
│   └── python/                         # Python Examples (ctypes / asyncio)
│       ├── decrypt_scenario.py         # File-based reference scenario
│       ├── in_memory_decrypt_demo.py   # Full buffer in-memory decryption
│       ├── streaming_decrypt_demo.py   # Real-time pipelined streaming decryption
│       ├── multi_stream_decrypt_demo.py # Multi-track (video+audio) AsyncIO concurrent decryption
│       ├── multi_track_metadata_demo.py # Metadata & probing demo in Python AsyncIO
│       ├── test_cancellation.py        # Thread-safe cancellation tests
│       └── sdk_demo.py                 # High-level Python SDK demo
└── third_party/
    └── shaka-packager/                 # Git submodule (Shaka Packager core)
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

**Windows (Visual Studio / MSVC):**
```bash
cmake -B build/
cmake --build build/ --config Release --target shaka_decryptor
```

**Linux / macOS:**
```bash
cmake -S . -B build/ -DCMAKE_BUILD_TYPE=Release
cmake --build build/ --target shaka_decryptor --parallel
```

The compiled library (`shaka_decryptor.dll`, `libshaka_decryptor.so`, or `libshaka_decryptor.dylib`) will be generated in `build/` (or `build/Release/`).

---

## High-Level SDK Usage

### Bun JS / TypeScript

```typescript
import { ShakaDecryptorSession } from "./sdk/bun/shaka_sdk";

// 1. One-Shot In-Memory Buffer Decryption (1-line!)
const decryptedBytes = ShakaDecryptorSession.decryptBuffer(encryptedBuffer, kidHex, keyHex);

// 2. Media Probing (Inspect video width, height, codecs, channels without playback)
const info = ShakaDecryptorSession.probe("decrypted_video.mp4");
console.log(`Resolution: ${info.streams[0].width}x${info.streams[0].height}, Codec: ${info.streams[0].codec}`);

// 3. Object-Oriented Session
const session = new ShakaDecryptorSession();
session.addKey(kidHex, keyHex);
session.addFileStream("encrypted_input.mp4", "decrypted_output.mp4");
session.run();
const stats = session.getStats();
console.log(`Throughput: ${stats.throughputMBps} MB/s in ${stats.executionDurationMs} ms`);
session.destroy();
```

### Python

```python
from sdk.python.shaka_sdk import ShakaDecryptorSession

# 1. One-Shot In-Memory Buffer Decryption (1-line!)
decrypted_bytes = ShakaDecryptorSession.decrypt_buffer(encrypted_bytes, kid_hex, key_hex)

# 2. Media Probing
info = ShakaDecryptorSession.probe("decrypted_video.mp4")
print(f"Format: {info.container_format}, Streams: {info.stream_count}")

# 3. Object-Oriented Session
with ShakaDecryptorSession() as session:
    session.add_key(kid_hex, key_hex)
    session.add_file_stream("encrypted_input.mp4", "decrypted_output.mp4")
    session.run()
    stats = session.get_stats()
    print(f"Decrypted {stats.total_bytes_read} bytes at {stats.throughput_mb_s:.2f} MB/s")
```

---

## Running Examples

### Bun Examples

```bash
# High-Level OOP SDK & One-Shot Buffer Demo
bun examples/bun/sdk_demo.ts

# Multi-track metadata tagging & native media probing
bun examples/bun/multi_track_metadata_demo.ts

# Progressive real-time streaming with Jitter Buffer (Sliding Window)
bun examples/bun/streaming_decrypt_demo.ts
```

### Python Examples

```bash
# High-Level OOP SDK & One-Shot Buffer Demo
python examples/python/sdk_demo.py

# Multi-track AsyncIO concurrent decryption & probing
python examples/python/multi_track_metadata_demo.py

# Thread-safe cancellation test suite
python examples/python/test_cancellation.py
```

---

## License

BSD 3-Clause License. See `LICENSE` for details.
