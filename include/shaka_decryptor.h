// Copyright 2026 Google LLC. All rights reserved.
//
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file or at
// https://developers.google.com/open-source/licenses/bsd

#ifndef PACKAGER_C_API_SHAKA_DECRYPTOR_H_
#define PACKAGER_C_API_SHAKA_DECRYPTOR_H_

#include <stdint.h>

#if defined(_WIN32)
#define SHAKA_DECRYPTOR_EXPORT __declspec(dllexport)
#else
#define SHAKA_DECRYPTOR_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

// Opaque context pointer.
typedef struct ShakaDecryptor ShakaDecryptor;

// Log severity levels (matching Abseil/Shaka conventions).
// SHAKA_LOG_LEVEL_INFO     = 0
// SHAKA_LOG_LEVEL_WARNING  = 1
// SHAKA_LOG_LEVEL_ERROR    = 2
// SHAKA_LOG_LEVEL_FATAL    = 3
// SHAKA_LOG_LEVEL_NONE     = 4  -- use with ShakaDecryptor_SetLogLevel to silence all logs
typedef enum {
  SHAKA_LOG_LEVEL_INFO    = 0,
  SHAKA_LOG_LEVEL_WARNING = 1,
  SHAKA_LOG_LEVEL_ERROR   = 2,
  SHAKA_LOG_LEVEL_FATAL   = 3,
  SHAKA_LOG_LEVEL_NONE    = 4
} ShakaLogLevel;

// Callback types.

// Called when data needs to be read from a stream (memory-based input).
// Returns number of bytes read, or -1 on error/EOF.
typedef int64_t (*ShakaReadFunc)(const char* name, void* buffer, uint64_t size, void* user_data);

// Called to query the total size of a stream (memory-based input).
// Returns size in bytes, or -1 if unknown.
typedef int64_t (*ShakaSizeFunc)(const char* name, void* user_data);

// Called periodically during processing to report progress.
// stream_name: identifier of the stream being processed (file path or callback name).
// bytes_processed: how many bytes have been read so far.
// total_bytes: total size in bytes (0 if unknown).
typedef void (*ShakaProgressFunc)(const char* stream_name, uint64_t bytes_processed, uint64_t total_bytes, void* user_data);

// Called for each Shaka internal log message at or above the configured minimum level.
// level: one of ShakaLogLevel values.
// message: null-terminated log string (includes Abseil prefix with file/line).
typedef void (*ShakaLogFunc)(int level, const char* message, void* user_data);

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// Create a new decryptor context. Must be destroyed with ShakaDecryptor_Destroy.
SHAKA_DECRYPTOR_EXPORT ShakaDecryptor* ShakaDecryptor_Create(void);

// Destroy a context and free all associated resources.
SHAKA_DECRYPTOR_EXPORT void ShakaDecryptor_Destroy(ShakaDecryptor* ctx);

// ---------------------------------------------------------------------------
// Error reporting
// ---------------------------------------------------------------------------

// Returns the last error message, or NULL if no error has occurred.
SHAKA_DECRYPTOR_EXPORT const char* ShakaDecryptor_GetLastError(ShakaDecryptor* ctx);

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Add a raw decryption key (hex strings, without dashes).
// Returns 0 on success, non-zero on failure.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_AddRawKey(ShakaDecryptor* ctx,
                                                    const char* key_id_hex,
                                                    const char* key_hex);

// ---------------------------------------------------------------------------
// Callbacks
// ---------------------------------------------------------------------------

// Set a progress callback.
// Called during both memory-based and file-based processing.
// For file-based streams, total_bytes reflects the file size (queried once at start).
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_SetProgressCallback(ShakaDecryptor* ctx,
                                                              ShakaProgressFunc cb,
                                                              void* user_data);

// Set a log callback to receive Shaka Packager internal log messages.
// Only messages at or above the level set by ShakaDecryptor_SetLogLevel are delivered.
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_SetLogCallback(ShakaDecryptor* ctx,
                                                         ShakaLogFunc cb,
                                                         void* user_data);

// Set the minimum log level for messages delivered to the log callback.
// Messages below this level are silently discarded.
// Default: SHAKA_LOG_LEVEL_INFO (all messages delivered).
// Pass SHAKA_LOG_LEVEL_NONE to suppress all log callbacks.
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_SetLogLevel(ShakaDecryptor* ctx, ShakaLogLevel level);

// Enable or disable Shaka's built-in console (stderr) logging.
// By default, Shaka writes all logs to stderr independently of any callback.
// Pass 0 to suppress console output entirely; pass 1 to restore it.
// Note: this is a process-wide Abseil setting -- it affects all contexts.
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_SetConsoleLogging(int enabled);

// ---------------------------------------------------------------------------
// Stream management
// ---------------------------------------------------------------------------

// Add a stream for decryption.
// Can be called multiple times for multiple tracks (e.g. separate video/audio files).
//
// name:             Human-readable identifier used in progress callbacks.
//                   For memory-based input, also used as the callback key.
//                   For file-based input (read_cb == NULL), pass the file path here.
// read_cb:          Called to read data. Pass NULL to use file-based I/O (name = file path).
// size_cb:          Called once to query total stream size. May be NULL (progress will show 0 total).
// stream_user_data: Opaque pointer passed back to read_cb and size_cb.
// output_path:      Destination file path for the decrypted output.
//
// Returns 0 on success, non-zero on failure.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_AddStream(ShakaDecryptor* ctx,
                                                    const char* name,
                                                    ShakaReadFunc read_cb,
                                                    ShakaSizeFunc size_cb,
                                                    void* stream_user_data,
                                                    const char* output_path);

// ---------------------------------------------------------------------------
// Execution
// ---------------------------------------------------------------------------

// Run the decryption. Blocks until all streams are processed.
// Returns 0 on success, non-zero on error (retrieve message with GetLastError).
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_Run(ShakaDecryptor* ctx);

#ifdef __cplusplus
}
#endif

#endif  // PACKAGER_C_API_SHAKA_DECRYPTOR_H_
