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

// Called to write decrypted data to an output stream (memory-based output).
// Returns number of bytes written, or -1 on error.
typedef int64_t (*ShakaWriteFunc)(const char* name, const void* buffer, uint64_t size, void* user_data);

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
// Stream options & metadata
// ---------------------------------------------------------------------------

// Granular configuration options for a single stream/track.
typedef struct ShakaStreamOptions {
  // Stream selector: "video", "audio", "text", "0", "1", etc. Pass NULL for default ("0").
  const char* stream_selector;
  // ISO-639-2 language tag (e.g. "hun", "eng", "fra", "deu"). Overrides input language metadata.
  const char* language;
  // User-facing track label/title (e.g. "Magyar 5.1 Szinkron", "English Audio").
  const char* track_label;
  // Container output format: "mp4", "mkv", "webm", "ts". Pass NULL to auto-detect from output name.
  const char* output_format;
  // Container input format: "mp4", "webm", "vtt", "ttml". Useful for headerless live streams.
  const char* input_format;
  // Forced subtitle flag: 1 = forced narrative subtitle, 0 = normal subtitle.
  int forced_subtitle;
  // User-specified bitrate in bits/sec (0 = auto-estimate).
  uint32_t bandwidth;
  // Trick mode frame sampling rate (0 = disabled).
  uint32_t trick_play_factor;
} ShakaStreamOptions;

// Stream metadata retrieved by probing.
typedef struct ShakaStreamMetadata {
  int stream_type;              // 0=Unknown, 1=Audio, 2=Video, 3=Text
  char codec[32];               // e.g. "avc1.640028", "hev1.1.6.L93.90", "mp4a.40.2", "wvtt"
  char language[16];            // e.g. "hun", "eng"
  int width;                    // Video width in pixels (0 for audio/text)
  int height;                   // Video height in pixels (0 for audio/text)
  double frame_rate;            // Video framerate (0 for audio/text)
  int audio_channels;           // Audio channels (e.g. 2, 6)
  int sample_rate;              // Audio sampling rate in Hz (e.g. 48000)
  double duration_seconds;      // Stream duration in seconds
} ShakaStreamMetadata;

// Aggregated media container information retrieved by probing.
typedef struct ShakaMediaInfo {
  int stream_count;
  ShakaStreamMetadata streams[16];
  char container_format[32];    // e.g. "MP4", "WebM", "MPEG2-TS"
  double duration_seconds;
} ShakaMediaInfo;

// ---------------------------------------------------------------------------
// Stream management
// ---------------------------------------------------------------------------

// Add a stream for decryption (file-based or memory input, file-based output).
// Can be called multiple times for multiple tracks (e.g. separate video/audio files).
// If multiple streams share the SAME output_path, Shaka multiplexes them into a single file.
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

// Add a stream for decryption with extended memory I/O support (in-memory input AND/OR output).
//
// output_path:      Destination file path, or an identifier for the output stream when write_cb is set.
// write_cb:         Called to write decrypted data directly to memory. Pass NULL for file-based output.
// write_user_data:  Opaque pointer passed back to write_cb.
//
// Returns 0 on success, non-zero on failure.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_AddStreamEx(ShakaDecryptor* ctx,
                                                      const char* name,
                                                      ShakaReadFunc read_cb,
                                                      ShakaSizeFunc size_cb,
                                                      void* stream_user_data,
                                                      const char* output_path,
                                                      ShakaWriteFunc write_cb,
                                                      void* write_user_data);

// Add a stream with granular options (language, track label, forced subtitle, format, etc.).
//
// options:          Pointer to ShakaStreamOptions, or NULL for defaults.
//
// Returns 0 on success, non-zero on failure.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_AddStreamWithOptions(
    ShakaDecryptor* ctx,
    const char* name,
    ShakaReadFunc read_cb,
    ShakaSizeFunc size_cb,
    void* stream_user_data,
    const char* output_path,
    ShakaWriteFunc write_cb,
    void* write_user_data,
    const ShakaStreamOptions* options);

// ---------------------------------------------------------------------------
// Execution & Control
// ---------------------------------------------------------------------------

// Run the decryption. Blocks until all streams are processed or cancelled.
// Returns 0 on success, non-zero on error/cancellation (retrieve message with GetLastError).
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_Run(ShakaDecryptor* ctx);

// Cancel an active decryption run. Thread-safe: can be called from any thread.
// Causes ShakaDecryptor_Run to immediately stop processing and exit with an error/cancelled status.
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_Cancel(ShakaDecryptor* ctx);

// Performance and throughput statistics.
typedef struct ShakaStats {
  uint64_t total_bytes_read;
  uint64_t total_bytes_written;
  double execution_duration_ms;
  double throughput_mb_per_sec;
} ShakaStats;

// Dynamic key request callback invoked when an unknown KID or PSSH is encountered.
// Should populate out_key_hex with the 32-character hex key string.
// Returns 0 if key was provided, non-zero if unavailable.
typedef int (*ShakaKeyRequestFunc)(
    const char* key_id_hex,
    const uint8_t* pssh_data,
    uint32_t pssh_size,
    char* out_key_hex,
    size_t out_key_max_len,
    void* user_data);

// Retrieve execution metrics and throughput from a completed run.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_GetStats(
    ShakaDecryptor* ctx,
    ShakaStats* out_stats);

// Set dynamic key request callback.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_SetKeyRequestCallback(
    ShakaDecryptor* ctx,
    ShakaKeyRequestFunc cb,
    void* user_data);

// One-shot, zero-setup in-memory buffer decryption.
// Decrypts an encrypted MP4/CMAF buffer completely in RAM.
// Allocated out_data buffer must be freed by caller using ShakaDecryptor_FreeBuffer().
// Returns 0 on success.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_DecryptBuffer(
    const uint8_t* in_data,
    uint64_t in_size,
    const char* kid_hex,
    const char* key_hex,
    uint8_t** out_data,
    uint64_t* out_size);

// Free buffer allocated by ShakaDecryptor_DecryptBuffer.
SHAKA_DECRYPTOR_EXPORT void ShakaDecryptor_FreeBuffer(uint8_t* buffer);

// Probe a media file to inspect its streams and extract metadata without full playback.
// Returns 0 on success, non-zero on error.
SHAKA_DECRYPTOR_EXPORT int ShakaDecryptor_ProbeMedia(
    const char* input_path,
    ShakaMediaInfo* out_info);

#ifdef __cplusplus
}
#endif

#endif  // PACKAGER_C_API_SHAKA_DECRYPTOR_H_
