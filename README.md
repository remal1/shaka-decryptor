# Shaka Decryptor

A lightweight, high-performance C/C++ shared library (`.dll` / `.so` / `.dylib`) wrapper around [Shaka Packager](https://github.com/shaka-project/shaka-packager) dedicated exclusively to **high-throughput media stream decryption**.

Designed for seamless integration into multi-language applications (**Bun JS**, **Node.js**, **Python**, **Go**, **Rust**, **C#**, **C/C++**, etc.) with both **file-based** and **in-memory / streaming (chunk/segment)** decryption, multi-track concurrency, real-time progress reporting, and granular log control.

---

## Key Features

- **Pure C ABI (`shaka_decryptor.h`)**: Effortlessly callable from any runtime via FFI (`bun:ffi`, Node.js `koffi`/`ffi-napi`, Python `ctypes`, Go `cgo`, Rust `bindgen`, C# `P/Invoke`).
- **Multi-Track & Multi-Threaded**: Decrypt multiple video resolutions, audio languages, and subtitle tracks concurrently on all CPU cores in a single pass.
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

## Project Structure & Examples

```text
shaka-decryptor/
├── CMakeLists.txt                 # Root CMake build configuration
├── include/
│   └── shaka_decryptor.h          # Public C API header
├── src/
│   └── shaka_decryptor.cc         # C API implementation
├── patches/
│   └── size_callback.patch        # Automated patch for Shaka Packager size callback
├── examples/
│   ├── bun/                       # Bun JS / TypeScript Examples (bun:ffi)
│   │   ├── shaka_bindings.ts          # Shared TypeScript FFI bindings module
│   │   ├── decrypt_worker.ts          # Dedicated Worker thread for non-blocking decryption
│   │   ├── decrypt_scenario.ts        # File-based scenario in Worker thread
│   │   ├── in_memory_decrypt_demo.ts  # In-memory RAM buffer decryption
│   │   ├── streaming_decrypt_demo.ts  # Real-time streaming with Jitter Buffer (Sliding Window)
│   │   └── multi_stream_decrypt_demo.ts # Multi-track (video+audio) concurrent decryption
│   └── python/                    # Python Examples (ctypes / asyncio)
│       ├── decrypt_scenario.py        # File-based reference scenario
│       ├── in_memory_decrypt_demo.py  # Full buffer in-memory decryption
│       ├── streaming_decrypt_demo.py  # Real-time pipelined streaming decryption
│       └── multi_stream_decrypt_demo.py # Multi-track (video+audio) AsyncIO concurrent decryption
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

**Windows (Visual Studio / Ninja / MSVC):**
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

## C API Reference (`shaka_decryptor.h`)

### Types & Enums

#### `ShakaLogLevel`
Defines minimum log severity filtering:
```c
typedef enum {
  SHAKA_LOG_LEVEL_INFO    = 0,
  SHAKA_LOG_LEVEL_WARNING = 1,
  SHAKA_LOG_LEVEL_ERROR   = 2,
  SHAKA_LOG_LEVEL_FATAL   = 3,
  SHAKA_LOG_LEVEL_NONE    = 4   // Suppresses all logs
} ShakaLogLevel;
```

#### Callbacks

| Callback Type | Signature | Description |
| :--- | :--- | :--- |
| **`ShakaReadFunc`** | `int64_t (*)(const char* name, void* buffer, uint64_t size, void* user_data)` | Called when Shaka reads input data from memory. Returns bytes read, `0` for EOF, `-1` on error. |
| **`ShakaWriteFunc`** | `int64_t (*)(const char* name, const void* buffer, uint64_t size, void* user_data)` | Called when Shaka writes decrypted data to memory. Returns bytes written, `-1` on error. |
| **`ShakaSizeFunc`** | `int64_t (*)(const char* name, void* user_data)` | Called to query total stream size in bytes. Returns size, or `-1` if unknown. |
| **`ShakaProgressFunc`** | `void (*)(const char* stream_name, uint64_t bytes_processed, uint64_t total_bytes, void* user_data)` | Periodically reports processed bytes and total bytes (0 if unknown). |
| **`ShakaLogFunc`** | `void (*)(int level, const char* message, void* user_data)` | Receives intercepted log messages at or above configured log level. |

---

### Functions

#### Lifecycle & Error Reporting

```c
// Creates a new decryptor context. Must be freed with ShakaDecryptor_Destroy.
ShakaDecryptor* ShakaDecryptor_Create(void);

// Destroys the context and frees all associated C++ resources.
void ShakaDecryptor_Destroy(ShakaDecryptor* ctx);

// Returns the last error string for the context, or NULL if no error occurred.
const char* ShakaDecryptor_GetLastError(ShakaDecryptor* ctx);
```

#### Key & Logging Configuration

```c
// Registers a raw decryption key (32-character Hex strings without dashes).
// Returns 0 on success, non-zero on error.
int ShakaDecryptor_AddRawKey(ShakaDecryptor* ctx, const char* key_id_hex, const char* key_hex);

// Sets the minimum severity level for logs delivered to ShakaLogFunc.
int ShakaDecryptor_SetLogLevel(ShakaDecryptor* ctx, ShakaLogLevel level);

// Enables (1) or disables (0) built-in stderr console logging across the entire process.
int ShakaDecryptor_SetConsoleLogging(int enabled);

// Registers a progress reporting callback.
int ShakaDecryptor_SetProgressCallback(ShakaDecryptor* ctx, ShakaProgressFunc cb, void* user_data);

// Registers a log interception callback.
int ShakaDecryptor_SetLogCallback(ShakaDecryptor* ctx, ShakaLogFunc cb, void* user_data);
```

#### Stream Registration

```c
// Registers an input stream for decryption (file or memory input, file output).
// Can be called multiple times on the same context for concurrent multi-track decryption.
int ShakaDecryptor_AddStream(ShakaDecryptor* ctx,
                             const char* name,
                             ShakaReadFunc read_cb,
                             ShakaSizeFunc size_cb,
                             void* stream_user_data,
                             const char* output_path);

// Extended stream registration supporting 100% in-memory input AND/OR output.
int ShakaDecryptor_AddStreamEx(ShakaDecryptor* ctx,
                               const char* name,
                               ShakaReadFunc read_cb,
                               ShakaSizeFunc size_cb,
                               void* stream_user_data,
                               const char* output_path,
                               ShakaWriteFunc write_cb,
                               void* write_user_data);
```

#### Execution

```c
// Runs the decryption engine across all registered streams.
// Blocks the calling thread until all streams are finalized.
// Returns 0 on success, non-zero on failure (check GetLastError).
int ShakaDecryptor_Run(ShakaDecryptor* ctx);
```

---

## Integration Examples

### Bun JS / TypeScript (`bun:ffi`)

In Bun, use `bun:ffi` to call the library directly and run `ShakaDecryptor_Run` inside a Worker thread so the main JS event loop remains 100% non-blocking:

```typescript
import { dlopen, FFIType } from "bun:ffi";

const { symbols } = dlopen("build/Release/shaka_decryptor.dll", {
  ShakaDecryptor_Create: { args: [], returns: FFIType.ptr },
  ShakaDecryptor_Destroy: { args: [FFIType.ptr], returns: FFIType.void },
  ShakaDecryptor_GetLastError: { args: [FFIType.ptr], returns: FFIType.cstring },
  ShakaDecryptor_AddRawKey: { args: [FFIType.ptr, FFIType.cstring, FFIType.cstring], returns: FFIType.i32 },
  ShakaDecryptor_AddStream: { args: [FFIType.ptr, FFIType.cstring, FFIType.ptr, FFIType.ptr, FFIType.ptr, FFIType.cstring], returns: FFIType.i32 },
  ShakaDecryptor_SetConsoleLogging: { args: [FFIType.i32], returns: FFIType.i32 },
  ShakaDecryptor_Run: { args: [FFIType.ptr], returns: FFIType.i32 },
});

// 1. Create context
const ctx = symbols.ShakaDecryptor_Create();

// 2. Add Keys (Hex)
symbols.ShakaDecryptor_AddRawKey(
  ctx,
  Buffer.from("9eb4050de44b4802932e27d75083e266\0"),
  Buffer.from("166634c675823c235a4a9446fad52e4d\0")
);

// 3. Add Stream(s)
symbols.ShakaDecryptor_AddStream(
  ctx,
  Buffer.from("encrypted_input.mp4\0"),
  null, null, null,
  Buffer.from("decrypted_output.mp4\0")
);

// 4. Run Decryption
const status = symbols.ShakaDecryptor_Run(ctx);
if (status !== 0) {
  console.error("Decryption failed:", symbols.ShakaDecryptor_GetLastError(ctx));
}

// 5. Clean up
symbols.ShakaDecryptor_Destroy(ctx);
```

#### Running Bun Examples

```bash
# 1. File-based decryption in Worker thread
bun examples/bun/decrypt_scenario.ts

# 2. In-Memory RAM buffer decryption
bun examples/bun/in_memory_decrypt_demo.ts

# 3. Progressive real-time streaming with Jitter Buffer (Sliding Window)
bun examples/bun/streaming_decrypt_demo.ts

# 4. Multi-track (1080p, 720p, Audio EN, Audio AU) concurrent decryption
bun examples/bun/multi_stream_decrypt_demo.ts
```

---

### Python (`ctypes` & `asyncio`)

```python
import ctypes
import asyncio

shaka = ctypes.CDLL("./build/Release/shaka_decryptor.dll")

shaka.ShakaDecryptor_Create.restype = ctypes.c_void_p
shaka.ShakaDecryptor_Destroy.argtypes = [ctypes.c_void_p]
shaka.ShakaDecryptor_AddRawKey.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
shaka.ShakaDecryptor_AddStream.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
shaka.ShakaDecryptor_Run.argtypes = [ctypes.c_void_p]

# 1. Create context
ctx = shaka.ShakaDecryptor_Create()

# 2. Add keys
shaka.ShakaDecryptor_AddRawKey(ctx, b"9eb4050de44b4802932e27d75083e266", b"166634c675823c235a4a9446fad52e4d")

# 3. Add stream
shaka.ShakaDecryptor_AddStream(ctx, b"encrypted_input.mp4", None, None, None, b"decrypted_output.mp4")

# 4. Run non-blocking in async event loop
async def decrypt():
    status = await asyncio.to_thread(shaka.ShakaDecryptor_Run, ctx)
    shaka.ShakaDecryptor_Destroy(ctx)
    return status

asyncio.run(decrypt())
```

#### Running Python Examples

```bash
# 1. File-based decryption scenario
python examples/python/decrypt_scenario.py

# 2. In-Memory RAM buffer decryption
python examples/python/in_memory_decrypt_demo.py

# 3. Real-time streaming decryption
python examples/python/streaming_decrypt_demo.py

# 4. Multi-track AsyncIO concurrent decryption
python examples/python/multi_stream_decrypt_demo.py
```

---

## Streaming vs In-Memory Architecture

| Strategy | Memory Footprint (50 segments / 110 MB) | C++ Decryption Speed | Best Used For |
| :--- | :--- | :--- | :--- |
| **In-Memory Buffer** (`in_memory_decrypt_demo`) | Total video size (~110 MB) | **~350–500 MB/s** | Short clips, trailers, segments where full RAM buffer is acceptable and maximum decryption speed is desired. |
| **Sliding Window Jitter Buffer** (`streaming_decrypt_demo` & `multi_stream_decrypt_demo`) | **Bounded (~1.5–5 MB per track)** | **~350–600 MB/s** | Live streams and large full-length movies (5–50 GB) to prevent high RAM consumption while absorbing network jitter. |

---

## License

BSD 3-Clause License. See `LICENSE` for details.
