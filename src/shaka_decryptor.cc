// Copyright 2026 Google LLC. All rights reserved.
//
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file or at
// https://developers.google.com/open-source/licenses/bsd

#include "shaka_decryptor.h"

#include <atomic>
#include <cstdio>
#include <cinttypes>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "absl/log/globals.h"
#include "absl/log/log_sink.h"
#include "absl/log/log_sink_registry.h"
#include "absl/log/log_entry.h"

#include "packager/packager.h"
#include "packager/status.h"
#include "packager/media/base/raw_key_source.h"

using namespace shaka;

// ---------------------------------------------------------------------------
// Internal data structures
// ---------------------------------------------------------------------------

struct StreamCbInfo {
  // Callback-based (memory) input. Null for file-based input.
  ShakaReadFunc read_cb = nullptr;
  ShakaSizeFunc size_cb = nullptr;
  void* user_data = nullptr;

  // Callback-based (memory) output. Null for file-based output.
  ShakaWriteFunc write_cb = nullptr;
  void* write_user_data = nullptr;

  // Progress tracking (used for both memory- and file-based streams).
  std::atomic<int64_t> bytes_read{0};
  int64_t total_size = -1;     // -1 = unknown
  std::string display_name;    // human-readable name reported in progress callbacks
};

struct ShakaDecryptor {
  PackagingParams params;
  std::vector<StreamDescriptor> streams;
  // Key: callback name (for callback:// streams) or file path (for file streams).
  std::map<std::string, StreamCbInfo> stream_cbs;
  BufferCallbackParams my_callback_params;

  std::string last_error_message;

  ShakaProgressFunc progress_cb = nullptr;
  void* progress_user_data = nullptr;

  ShakaLogFunc log_cb = nullptr;
  void* log_user_data = nullptr;
  int min_log_level = SHAKA_LOG_LEVEL_INFO;  // messages below this are dropped

  std::mutex cb_mutex;
};

// ---------------------------------------------------------------------------
// Global state for callback routing
// ---------------------------------------------------------------------------

// Maps a stream lookup key -> owning context, so our global C callbacks can
// find the right ShakaDecryptor when Shaka calls back.
static std::mutex g_contexts_mutex;
static std::map<std::string, ShakaDecryptor*> g_contexts_map;

// ---------------------------------------------------------------------------
// Progress helper (shared by both memory and file paths)
// ---------------------------------------------------------------------------

static void MaybeReportProgress(ShakaDecryptor* ctx, const std::string& key) {
  if (!ctx || !ctx->progress_cb) return;

  std::string disp_name;
  int64_t bytes_read = 0;
  int64_t total = 0;
  void* user_data = ctx->progress_user_data;

  {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    auto it = ctx->stream_cbs.find(key);
    if (it == ctx->stream_cbs.end()) return;
    disp_name = it->second.display_name;
    bytes_read = it->second.bytes_read.load();
    total = it->second.total_size;
  }

  ctx->progress_cb(
      disp_name.c_str(),
      static_cast<uint64_t>(bytes_read),
      total > 0 ? static_cast<uint64_t>(total) : 0,
      user_data);
}

// ---------------------------------------------------------------------------
// Shaka BufferCallbackParams read/size/write functions
// ---------------------------------------------------------------------------

static int64_t CbReadFunc(const std::string& name, void* buffer, uint64_t size) {
  ShakaDecryptor* ctx = nullptr;
  ShakaReadFunc read_cb = nullptr;
  void* user_data = nullptr;

  {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    auto ctx_it = g_contexts_map.find(name);
    if (ctx_it == g_contexts_map.end()) return -1;

    ctx = ctx_it->second;
    auto stream_it = ctx->stream_cbs.find(name);
    if (stream_it == ctx->stream_cbs.end()) return -1;

    read_cb   = stream_it->second.read_cb;
    user_data = stream_it->second.user_data;
  }

  if (!read_cb) return -1;

  int64_t bytes_read = read_cb(name.c_str(), buffer, size, user_data);
  if (bytes_read > 0) {
    {
      std::lock_guard<std::mutex> lock(g_contexts_mutex);
      auto stream_it = ctx->stream_cbs.find(name);
      if (stream_it != ctx->stream_cbs.end()) {
        stream_it->second.bytes_read += bytes_read;
      }
    }
    MaybeReportProgress(ctx, name);
  }
  return bytes_read;
}

static int64_t CbWriteFunc(const std::string& name, const void* buffer, uint64_t size) {
  ShakaDecryptor* ctx = nullptr;
  ShakaWriteFunc write_cb = nullptr;
  void* user_data = nullptr;

  {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    auto ctx_it = g_contexts_map.find(name);
    if (ctx_it == g_contexts_map.end()) return -1;

    ctx = ctx_it->second;
    auto stream_it = ctx->stream_cbs.find(name);
    if (stream_it == ctx->stream_cbs.end()) return -1;

    write_cb  = stream_it->second.write_cb;
    user_data = stream_it->second.write_user_data;
  }

  if (!write_cb) return -1;
  return write_cb(name.c_str(), buffer, size, user_data);
}

// ---------------------------------------------------------------------------
// File-based progress tracking thread
//
// For file-based (non-callback) streams we can't intercept every read, but we
// can poll the output file size compared to the expected input size to provide
// approximate progress.  We do this in a simple polling approach by running a
// background thread during ShakaDecryptor_Run().
// ---------------------------------------------------------------------------

#ifdef _WIN32
#include <windows.h>
static int64_t GetFileSize64(const std::string& path) {
  WIN32_FILE_ATTRIBUTE_DATA data;
  if (!GetFileAttributesExA(path.c_str(), GetFileExInfoStandard, &data))
    return -1;
  LARGE_INTEGER li;
  li.HighPart = data.nFileSizeHigh;
  li.LowPart  = data.nFileSizeLow;
  return li.QuadPart;
}
#else
#include <sys/stat.h>
static int64_t GetFileSize64(const std::string& path) {
  struct stat st;
  if (stat(path.c_str(), &st) != 0) return -1;
  return static_cast<int64_t>(st.st_size);
}
#endif

// ---------------------------------------------------------------------------
// Abseil log interceptor
// ---------------------------------------------------------------------------

class ShakaLogInterceptor : public absl::LogSink {
 public:
  void Send(const absl::LogEntry& entry) override {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    int level = static_cast<int>(entry.log_severity());
    std::string msg(
        entry.text_message_with_prefix_and_newline().data(),
        entry.text_message_with_prefix_and_newline().size());

    // Notify each unique context once (there may be multiple streams sharing a context).
    std::vector<ShakaDecryptor*> notified;
    for (auto& pair : g_contexts_map) {
      ShakaDecryptor* ctx = pair.second;
      if (!ctx || !ctx->log_cb) continue;
      if (level < ctx->min_log_level) continue;

      bool already = false;
      for (auto* n : notified) if (n == ctx) { already = true; break; }
      if (!already) {
        ctx->log_cb(level, msg.c_str(), ctx->log_user_data);
        notified.push_back(ctx);
      }
    }
  }
};

static ShakaLogInterceptor* g_log_interceptor = nullptr;

// ---------------------------------------------------------------------------
// Public API implementation
// ---------------------------------------------------------------------------

ShakaDecryptor* ShakaDecryptor_Create(void) {
  ShakaDecryptor* ctx = new ShakaDecryptor();
  ctx->params.decryption_params.key_provider = KeyProvider::kRawKey;
  ctx->my_callback_params.read_func  = CbReadFunc;
  ctx->my_callback_params.write_func = CbWriteFunc;
  // Default segment duration required by ChunkingHandler.
  ctx->params.chunking_params.segment_duration_in_seconds = 6.0;
  ctx->params.chunking_params.subsegment_duration_in_seconds = 0.0;
  return ctx;
}

void ShakaDecryptor_Destroy(ShakaDecryptor* ctx) {
  if (!ctx) return;
  {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    for (const auto& kv : ctx->stream_cbs) {
      g_contexts_map.erase(kv.first);
    }
  }
  delete ctx;
}

const char* ShakaDecryptor_GetLastError(ShakaDecryptor* ctx) {
  if (!ctx || ctx->last_error_message.empty()) return nullptr;
  return ctx->last_error_message.c_str();
}

int ShakaDecryptor_AddRawKey(ShakaDecryptor* ctx,
                             const char* key_id_hex,
                             const char* key_hex) {
  if (!ctx || !key_id_hex || !key_hex) return -1;

  auto hex_to_bytes = [](const std::string& hex) {
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i + 1 < hex.size(); i += 2) {
      bytes.push_back(static_cast<uint8_t>(strtol(hex.substr(i, 2).c_str(), nullptr, 16)));
    }
    return bytes;
  };

  RawKeyParams::KeyInfo key_info;
  key_info.key_id = hex_to_bytes(key_id_hex);
  key_info.key    = hex_to_bytes(key_hex);

  // Map under the empty label (matches all streams) and the binary key-id string.
  ctx->params.decryption_params.raw_key.key_map[""] = key_info;
  std::string kid_str(key_info.key_id.begin(), key_info.key_id.end());
  ctx->params.decryption_params.raw_key.key_map[kid_str] = key_info;
  return 0;
}

int ShakaDecryptor_SetProgressCallback(ShakaDecryptor* ctx,
                                       ShakaProgressFunc cb,
                                       void* user_data) {
  if (!ctx) return -1;
  ctx->progress_cb        = cb;
  ctx->progress_user_data = user_data;
  return 0;
}

int ShakaDecryptor_SetLogCallback(ShakaDecryptor* ctx,
                                  ShakaLogFunc cb,
                                  void* user_data) {
  if (!ctx) return -1;
  ctx->log_cb        = cb;
  ctx->log_user_data = user_data;

  std::lock_guard<std::mutex> lock(g_contexts_mutex);
  if (!g_log_interceptor) {
    g_log_interceptor = new ShakaLogInterceptor();
    absl::AddLogSink(g_log_interceptor);
  }
  return 0;
}

int ShakaDecryptor_SetLogLevel(ShakaDecryptor* ctx, ShakaLogLevel level) {
  if (!ctx) return -1;
  ctx->min_log_level = static_cast<int>(level);

  // Also tell Abseil's global minimum so messages below the threshold are
  // not even generated (saves CPU).  Use the lowest requested level across
  // all contexts; we use kInfo as a safe fallback here.
  // (SHAKA_LOG_LEVEL_NONE == 4 maps to kInfinity which silences everything.)
  absl::LogSeverityAtLeast absl_level;
  switch (level) {
    case SHAKA_LOG_LEVEL_WARNING: absl_level = absl::LogSeverityAtLeast::kWarning; break;
    case SHAKA_LOG_LEVEL_ERROR:   absl_level = absl::LogSeverityAtLeast::kError;   break;
    case SHAKA_LOG_LEVEL_FATAL:   absl_level = absl::LogSeverityAtLeast::kFatal;   break;
    case SHAKA_LOG_LEVEL_NONE:    absl_level = absl::LogSeverityAtLeast::kInfinity; break;
    default:                      absl_level = absl::LogSeverityAtLeast::kInfo;    break;
  }
  absl::SetMinLogLevel(absl_level);
  return 0;
}

int ShakaDecryptor_SetConsoleLogging(int enabled) {
  // Abseil writes to stderr via its built-in StderrLogSink.
  // Setting the stderr threshold to kInfinity effectively disables it.
  if (enabled) {
    absl::SetStderrThreshold(absl::LogSeverityAtLeast::kInfo);
  } else {
    absl::SetStderrThreshold(absl::LogSeverityAtLeast::kInfinity);
  }
  return 0;
}

int ShakaDecryptor_AddStreamEx(ShakaDecryptor* ctx,
                               const char* name,
                               ShakaReadFunc read_cb,
                               ShakaSizeFunc size_cb,
                               void* stream_user_data,
                               const char* output_path,
                               ShakaWriteFunc write_cb,
                               void* write_user_data) {
  if (!ctx || !name || !output_path) return -1;

  std::string input_uri;
  std::string input_lookup_key;

  std::string output_uri;
  std::string output_lookup_key;

  StreamCbInfo in_cb_info;
  in_cb_info.display_name = name;

  if (read_cb) {
    // Memory-based input via callback://<addr>/<name>
    char addr_buf[64];
    snprintf(addr_buf, sizeof(addr_buf), "callback://%" PRIu64 "/",
             reinterpret_cast<uint64_t>(&ctx->my_callback_params));
    input_uri        = std::string(addr_buf) + name;
    input_lookup_key = name;

    in_cb_info.read_cb   = read_cb;
    in_cb_info.size_cb   = size_cb;
    in_cb_info.user_data = stream_user_data;

    // Pre-query size for progress reporting.
    if (size_cb) {
      in_cb_info.total_size = size_cb(name, stream_user_data);
    }
  } else {
    // File-based input: name IS the file path.
    input_uri        = name;
    input_lookup_key = name;
    in_cb_info.total_size = GetFileSize64(name);
  }

  StreamCbInfo& stored_in = ctx->stream_cbs[input_lookup_key];
  stored_in.display_name = in_cb_info.display_name;
  stored_in.read_cb      = in_cb_info.read_cb;
  stored_in.size_cb      = in_cb_info.size_cb;
  stored_in.user_data    = in_cb_info.user_data;
  stored_in.total_size   = in_cb_info.total_size;
  stored_in.bytes_read.store(0);

  if (write_cb) {
    char addr_buf[64];
    snprintf(addr_buf, sizeof(addr_buf), "callback://%" PRIu64 "/",
             reinterpret_cast<uint64_t>(&ctx->my_callback_params));
    output_uri        = std::string(addr_buf) + output_path;
    output_lookup_key = output_path;

    StreamCbInfo& stored_out = ctx->stream_cbs[output_lookup_key];
    stored_out.write_cb        = write_cb;
    stored_out.write_user_data = write_user_data;
  } else {
    output_uri = output_path;
  }

  StreamDescriptor stream;
  stream.input           = input_uri;
  stream.stream_selector = "0";
  stream.output          = output_uri;
  ctx->streams.push_back(stream);

  {
    std::lock_guard<std::mutex> lock(g_contexts_mutex);
    g_contexts_map[input_lookup_key] = ctx;
    if (write_cb) {
      g_contexts_map[output_lookup_key] = ctx;
    }
  }

  return 0;
}

int ShakaDecryptor_AddStream(ShakaDecryptor* ctx,
                             const char* name,
                             ShakaReadFunc read_cb,
                             ShakaSizeFunc size_cb,
                             void* stream_user_data,
                             const char* output_path) {
  return ShakaDecryptor_AddStreamEx(ctx, name, read_cb, size_cb, stream_user_data, output_path, nullptr, nullptr);
}

int ShakaDecryptor_Run(ShakaDecryptor* ctx) {
  if (!ctx) return -1;

  // -----------------------------------------------------------------------
  // File-based progress polling thread.
  // For file-based streams (no read_cb) we start a background thread that
  // polls the output file sizes and maps them back to approximate input
  // progress (using the ratio of output written / total input size).
  // -----------------------------------------------------------------------
  std::atomic<bool> run_done{false};
  std::thread progress_thread;

  if (ctx->progress_cb) {
    // Collect file-based streams that need polling.
    struct FilePollEntry {
      std::string lookup_key;
      std::string output_path;
    };
    std::vector<FilePollEntry> file_entries;
    for (auto& sd : ctx->streams) {
      auto& kv = ctx->stream_cbs;
      // A file-based stream has no read_cb.
      auto it = kv.find(sd.input);   // for file streams, key == input == file path
      if (it != kv.end() && !it->second.read_cb) {
        file_entries.push_back({sd.input, sd.output});
      }
    }

    if (!file_entries.empty()) {
      progress_thread = std::thread([ctx, file_entries, &run_done]() {
        while (!run_done.load()) {
          {
            std::lock_guard<std::mutex> lock(g_contexts_mutex);
            for (auto& entry : file_entries) {
              auto it = ctx->stream_cbs.find(entry.lookup_key);
              if (it == ctx->stream_cbs.end()) continue;
              StreamCbInfo& info = it->second;

              // Approximate: use output bytes written as proxy for processed input.
              int64_t out_size = GetFileSize64(entry.output_path);
              if (out_size > 0) {
                // Store in bytes_read so MaybeReportProgress works.
                info.bytes_read.store(out_size);
              }
              MaybeReportProgress(ctx, entry.lookup_key);
            }
          }
          // Poll every ~200 ms.
#ifdef _WIN32
          Sleep(200);
#else
          usleep(200000);
#endif
        }
        // Final progress report at 100%.
        std::lock_guard<std::mutex> lock(g_contexts_mutex);
        for (auto& entry : file_entries) {
          auto it = ctx->stream_cbs.find(entry.lookup_key);
          if (it == ctx->stream_cbs.end()) continue;
          StreamCbInfo& info = it->second;
          if (info.total_size > 0)
            info.bytes_read.store(info.total_size);
          MaybeReportProgress(ctx, entry.lookup_key);
        }
      });
    }
  }

  Packager packager;
  Status status = packager.Initialize(ctx->params, ctx->streams);
  if (!status.ok()) {
    run_done.store(true);
    if (progress_thread.joinable()) progress_thread.join();
    ctx->last_error_message = status.ToString();
    return -2;
  }

  status = packager.Run();
  run_done.store(true);
  if (progress_thread.joinable()) progress_thread.join();

  if (!status.ok()) {
    ctx->last_error_message = status.ToString();
    return -3;
  }

  return 0;
}
