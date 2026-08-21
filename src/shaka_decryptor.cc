#ifndef NOMINMAX
#define NOMINMAX
#endif

// Copyright 2026 Google LLC. All rights reserved.
//
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file or at
// https://developers.google.com/open-source/licenses/bsd

#include "shaka_decryptor.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <vector>
#include <memory>
#include <thread>

#include "absl/log/globals.h"
#include "absl/log/log_sink.h"
#include "absl/log/log_sink_registry.h"
#include "absl/log/log_entry.h"

#include "packager/packager.h"
#include "packager/status.h"
#include "packager/media/base/raw_key_source.h"
#include "packager/file.h"
#include "packager/media/base/container_names.h"
#include "packager/media/base/media_parser.h"
#include "packager/media/base/stream_info.h"
#include "packager/media/base/video_stream_info.h"
#include "packager/media/base/audio_stream_info.h"
#include "packager/media/base/text_stream_info.h"
#include "packager/media/formats/mp4/mp4_media_parser.h"
#include "packager/media/formats/webm/webm_media_parser.h"
#include "packager/media/formats/mp2t/mp2t_media_parser.h"
#include "packager/media/formats/webvtt/webvtt_parser.h"

using namespace shaka;
using namespace shaka::media;

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

  // Active execution and cancellation management
  std::mutex packager_mutex;
  Packager* active_packager = nullptr;
  std::atomic<bool> is_cancelled{false};

  // Performance & I/O statistics
  std::atomic<uint64_t> total_bytes_read{0};
  std::atomic<uint64_t> total_bytes_written{0};
  double last_duration_ms = 0.0;
  double last_throughput_mb_s = 0.0;

  // Dynamic key resolution callback
  ShakaKeyRequestFunc key_req_cb = nullptr;
  void* key_req_user_data = nullptr;
};

// ---------------------------------------------------------------------------
// Global state for callback routing
// ---------------------------------------------------------------------------

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
    ctx->total_bytes_read += static_cast<uint64_t>(bytes_read);
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
  int64_t written = write_cb(name.c_str(), buffer, size, user_data);
  if (written > 0) {
    ctx->total_bytes_written += static_cast<uint64_t>(written);
  }
  return written;
}

// ---------------------------------------------------------------------------
// File-based progress tracking helper
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
  ctx->params.chunking_params.segment_duration_in_seconds = 6.0;
  ctx->params.chunking_params.subsegment_duration_in_seconds = 0.0;
  return ctx;
}

void ShakaDecryptor_Destroy(ShakaDecryptor* ctx) {
  if (!ctx) return;
  ShakaDecryptor_Cancel(ctx);
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
  if (enabled) {
    absl::SetStderrThreshold(absl::LogSeverityAtLeast::kInfo);
  } else {
    absl::SetStderrThreshold(absl::LogSeverityAtLeast::kInfinity);
  }
  return 0;
}

int ShakaDecryptor_AddStreamWithOptions(
    ShakaDecryptor* ctx,
    const char* name,
    ShakaReadFunc read_cb,
    ShakaSizeFunc size_cb,
    void* stream_user_data,
    const char* output_path,
    ShakaWriteFunc write_cb,
    void* write_user_data,
    const ShakaStreamOptions* options) {
  if (!ctx || !name || !output_path) return -1;

  std::string input_uri;
  std::string input_lookup_key;

  std::string output_uri;
  std::string output_lookup_key;

  StreamCbInfo in_cb_info;
  in_cb_info.display_name = name;

  if (read_cb) {
    char addr_buf[64];
    snprintf(addr_buf, sizeof(addr_buf), "callback://%" PRIu64 "/",
             reinterpret_cast<uint64_t>(&ctx->my_callback_params));
    input_uri        = std::string(addr_buf) + name;
    input_lookup_key = name;

    in_cb_info.read_cb   = read_cb;
    in_cb_info.size_cb   = size_cb;
    in_cb_info.user_data = stream_user_data;

    if (size_cb) {
      in_cb_info.total_size = size_cb(name, stream_user_data);
    }
  } else {
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
  stream.input  = input_uri;
  stream.output = output_uri;

  if (options) {
    stream.stream_selector = (options->stream_selector && strlen(options->stream_selector) > 0)
                                 ? options->stream_selector
                                 : "0";
    if (options->language && strlen(options->language) > 0) {
      stream.language = options->language;
    }
    if (options->track_label && strlen(options->track_label) > 0) {
      stream.dash_label = options->track_label;
      stream.hls_name   = options->track_label;
    }
    if (options->output_format && strlen(options->output_format) > 0) {
      stream.output_format = options->output_format;
    }
    if (options->input_format && strlen(options->input_format) > 0) {
      stream.input_format = options->input_format;
    }
    if (options->forced_subtitle) {
      stream.forced_subtitle = true;
    }
    if (options->bandwidth > 0) {
      stream.bandwidth = options->bandwidth;
    }
    if (options->trick_play_factor > 0) {
      stream.trick_play_factor = options->trick_play_factor;
    }
  } else {
    stream.stream_selector = "0";
  }

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

int ShakaDecryptor_AddStreamEx(ShakaDecryptor* ctx,
                               const char* name,
                               ShakaReadFunc read_cb,
                               ShakaSizeFunc size_cb,
                               void* stream_user_data,
                               const char* output_path,
                               ShakaWriteFunc write_cb,
                               void* write_user_data) {
  return ShakaDecryptor_AddStreamWithOptions(
      ctx, name, read_cb, size_cb, stream_user_data, output_path, write_cb, write_user_data, nullptr);
}

int ShakaDecryptor_AddStream(ShakaDecryptor* ctx,
                             const char* name,
                             ShakaReadFunc read_cb,
                             ShakaSizeFunc size_cb,
                             void* stream_user_data,
                             const char* output_path) {
  return ShakaDecryptor_AddStreamWithOptions(
      ctx, name, read_cb, size_cb, stream_user_data, output_path, nullptr, nullptr, nullptr);
}

int ShakaDecryptor_Cancel(ShakaDecryptor* ctx) {
  if (!ctx) return -1;
  ctx->is_cancelled.store(true);
  std::lock_guard<std::mutex> lock(ctx->packager_mutex);
  if (ctx->active_packager) {
    ctx->active_packager->Cancel();
  }
  return 0;
}

int ShakaDecryptor_Run(ShakaDecryptor* ctx) {
  if (!ctx) return -1;

  if (ctx->is_cancelled.load()) {
    ctx->last_error_message = "Cancelled before execution";
    return -4;
  }

  auto start_time = std::chrono::high_resolution_clock::now();

  std::atomic<bool> run_done{false};
  std::thread progress_thread;

  if (ctx->progress_cb) {
    struct FilePollEntry {
      std::string lookup_key;
      std::string output_path;
    };
    std::vector<FilePollEntry> file_entries;
    for (auto& sd : ctx->streams) {
      auto& kv = ctx->stream_cbs;
      auto it = kv.find(sd.input);
      if (it != kv.end() && !it->second.read_cb) {
        file_entries.push_back({sd.input, sd.output});
      }
    }

    if (!file_entries.empty()) {
      progress_thread = std::thread([ctx, file_entries, &run_done]() {
        while (!run_done.load() && !ctx->is_cancelled.load()) {
          {
            std::lock_guard<std::mutex> lock(g_contexts_mutex);
            for (auto& entry : file_entries) {
              auto it = ctx->stream_cbs.find(entry.lookup_key);
              if (it == ctx->stream_cbs.end()) continue;
              StreamCbInfo& info = it->second;

              int64_t out_size = GetFileSize64(entry.output_path);
              if (out_size > 0) {
                info.bytes_read.store(out_size);
              }
              MaybeReportProgress(ctx, entry.lookup_key);
            }
          }
#ifdef _WIN32
          Sleep(200);
#else
          usleep(200000);
#endif
        }
        if (!ctx->is_cancelled.load()) {
          std::lock_guard<std::mutex> lock(g_contexts_mutex);
          for (auto& entry : file_entries) {
            auto it = ctx->stream_cbs.find(entry.lookup_key);
            if (it == ctx->stream_cbs.end()) continue;
            StreamCbInfo& info = it->second;
            if (info.total_size > 0)
              info.bytes_read.store(info.total_size);
            MaybeReportProgress(ctx, entry.lookup_key);
          }
        }
      });
    }
  }

  Packager packager;
  {
    std::lock_guard<std::mutex> lock(ctx->packager_mutex);
    if (ctx->is_cancelled.load()) {
      ctx->last_error_message = "Cancelled before execution";
      return -4;
    }
    ctx->active_packager = &packager;
  }

  Status status = packager.Initialize(ctx->params, ctx->streams);
  if (!status.ok()) {
    {
      std::lock_guard<std::mutex> lock(ctx->packager_mutex);
      ctx->active_packager = nullptr;
    }
    run_done.store(true);
    if (progress_thread.joinable()) progress_thread.join();
    ctx->last_error_message = status.ToString();
    return -2;
  }

  status = packager.Run();
  {
    std::lock_guard<std::mutex> lock(ctx->packager_mutex);
    ctx->active_packager = nullptr;
  }
  run_done.store(true);
  if (progress_thread.joinable()) progress_thread.join();

  auto end_time = std::chrono::high_resolution_clock::now();
  std::chrono::duration<double, std::milli> elapsed = end_time - start_time;
  ctx->last_duration_ms = elapsed.count();
  double secs = ctx->last_duration_ms / 1000.0;
  uint64_t total_bytes = ctx->total_bytes_read.load();
  if (total_bytes == 0) {
    for (const auto& kv : ctx->stream_cbs) {
      if (kv.second.total_size > 0) total_bytes += kv.second.total_size;
    }
  }
  ctx->last_throughput_mb_s = (secs > 0.0) ? ((total_bytes / 1024.0 / 1024.0) / secs) : 0.0;

  if (!status.ok()) {
    ctx->last_error_message = status.ToString();
    return ctx->is_cancelled.load() ? -4 : -3;
  }

  return 0;
}

int ShakaDecryptor_GetStats(ShakaDecryptor* ctx, ShakaStats* out_stats) {
  if (!ctx || !out_stats) return -1;
  out_stats->total_bytes_read = ctx->total_bytes_read.load();
  out_stats->total_bytes_written = ctx->total_bytes_written.load();
  out_stats->execution_duration_ms = ctx->last_duration_ms;
  out_stats->throughput_mb_per_sec = ctx->last_throughput_mb_s;
  return 0;
}

int ShakaDecryptor_SetKeyRequestCallback(ShakaDecryptor* ctx,
                                         ShakaKeyRequestFunc cb,
                                         void* user_data) {
  if (!ctx) return -1;
  ctx->key_req_cb = cb;
  ctx->key_req_user_data = user_data;
  return 0;
}

// ---------------------------------------------------------------------------
// One-Shot In-Memory Buffer Decryption Implementation
// ---------------------------------------------------------------------------

struct BufferMemContext {
  const uint8_t* in_data;
  uint64_t in_size;
  uint64_t in_pos;
  std::vector<uint8_t> out_buf;
};

static int64_t MemReadFunc(const char*, void* buffer, uint64_t size, void* user_data) {
  auto* mem = static_cast<BufferMemContext*>(user_data);
  if (mem->in_pos >= mem->in_size) return 0;
  uint64_t to_read = (std::min)(size, mem->in_size - mem->in_pos);
  memcpy(buffer, mem->in_data + mem->in_pos, to_read);
  mem->in_pos += to_read;
  return static_cast<int64_t>(to_read);
}

static int64_t MemSizeFunc(const char*, void* user_data) {
  auto* mem = static_cast<BufferMemContext*>(user_data);
  return static_cast<int64_t>(mem->in_size);
}

static int64_t MemWriteFunc(const char*, const void* buffer, uint64_t size, void* user_data) {
  auto* mem = static_cast<BufferMemContext*>(user_data);
  const uint8_t* src = static_cast<const uint8_t*>(buffer);
  mem->out_buf.insert(mem->out_buf.end(), src, src + size);
  return static_cast<int64_t>(size);
}

int ShakaDecryptor_DecryptBuffer(
    const uint8_t* in_data,
    uint64_t in_size,
    const char* kid_hex,
    const char* key_hex,
    uint8_t** out_data,
    uint64_t* out_size) {
  if (!in_data || in_size == 0 || !kid_hex || !key_hex || !out_data || !out_size)
    return -1;
  *out_data = nullptr;
  *out_size = 0;

  ShakaDecryptor* ctx = ShakaDecryptor_Create();
  if (!ctx) return -2;

  BufferMemContext mem{in_data, in_size, 0, {}};
  mem.out_buf.reserve(in_size);

  ShakaDecryptor_SetConsoleLogging(0);
  ShakaDecryptor_SetLogLevel(ctx, SHAKA_LOG_LEVEL_NONE);
  ShakaDecryptor_AddRawKey(ctx, kid_hex, key_hex);

  ShakaDecryptor_AddStreamEx(
      ctx,
      "mem_in.mp4",
      MemReadFunc,
      MemSizeFunc,
      &mem,
      "mem_out.mp4",
      MemWriteFunc,
      &mem);

  int res = ShakaDecryptor_Run(ctx);
  ShakaDecryptor_Destroy(ctx);

  if (res != 0 || mem.out_buf.empty()) return res != 0 ? res : -3;

  uint8_t* result_buf = static_cast<uint8_t*>(malloc(mem.out_buf.size()));
  if (!result_buf) return -4;

  memcpy(result_buf, mem.out_buf.data(), mem.out_buf.size());
  *out_data = result_buf;
  *out_size = mem.out_buf.size();
  return 0;
}

void ShakaDecryptor_FreeBuffer(uint8_t* buffer) {
  if (buffer) free(buffer);
}

// ---------------------------------------------------------------------------
// Media Probing Implementation
// ---------------------------------------------------------------------------

int ShakaDecryptor_ProbeMedia(const char* input_path, ShakaMediaInfo* out_info) {
  if (!input_path || !out_info) return -1;
  memset(out_info, 0, sizeof(*out_info));

  File* file = File::Open(input_path, "r");
  if (!file) return -2;

  const size_t kBufSize = 65536;
  std::vector<uint8_t> buf(kBufSize);
  int64_t bytes_read = file->Read(buf.data(), kBufSize);
  if (bytes_read <= 0) {
    file->Close();
    return -3;
  }

  MediaContainerName container = DetermineContainer(buf.data(), static_cast<int>(bytes_read));
  if (container == CONTAINER_UNKNOWN) {
    container = DetermineContainerFromFileName(input_path);
  }

  std::unique_ptr<MediaParser> parser;
  switch (container) {
    case CONTAINER_MOV:
      snprintf(out_info->container_format, sizeof(out_info->container_format), "MP4/ISOBMFF");
      parser.reset(new mp4::MP4MediaParser());
      break;
    case CONTAINER_WEBM:
      snprintf(out_info->container_format, sizeof(out_info->container_format), "WebM/MKV");
      parser.reset(new WebMMediaParser());
      break;
    case CONTAINER_MPEG2TS:
      snprintf(out_info->container_format, sizeof(out_info->container_format), "MPEG2-TS");
      parser.reset(new mp2t::Mp2tMediaParser());
      break;
    case CONTAINER_WEBVTT:
      snprintf(out_info->container_format, sizeof(out_info->container_format), "WebVTT");
      parser.reset(new WebVttParser());
      break;
    default:
      snprintf(out_info->container_format, sizeof(out_info->container_format), "Unknown");
      file->Close();
      return -4;
  }

  bool init_received = false;
  auto init_cb = [&out_info, &init_received](const std::vector<std::shared_ptr<StreamInfo>>& stream_infos) {
    init_received = true;
    out_info->stream_count = static_cast<int>((std::min)(stream_infos.size(), static_cast<size_t>(16)));
    for (int i = 0; i < out_info->stream_count; ++i) {
      const auto& s = stream_infos[i];
      ShakaStreamMetadata& meta = out_info->streams[i];

      meta.stream_type = static_cast<int>(s->stream_type());
      snprintf(meta.codec, sizeof(meta.codec), "%s", s->codec_string().c_str());
      snprintf(meta.language, sizeof(meta.language), "%s", s->language().c_str());

      if (s->time_scale() > 0 && s->duration() > 0) {
        meta.duration_seconds = static_cast<double>(s->duration()) / s->time_scale();
        out_info->duration_seconds = (std::max)(out_info->duration_seconds, meta.duration_seconds);
      }

      if (s->stream_type() == kStreamVideo) {
        auto* v = static_cast<VideoStreamInfo*>(s.get());
        meta.width = v->width();
        meta.height = v->height();
      } else if (s->stream_type() == kStreamAudio) {
        auto* a = static_cast<AudioStreamInfo*>(s.get());
        meta.audio_channels = a->num_channels();
        meta.sample_rate = a->sampling_frequency();
      }
    }
  };

  auto sample_cb = [](uint32_t, std::shared_ptr<MediaSample>) { return true; };
  auto text_cb   = [](uint32_t, std::shared_ptr<TextSample>) { return true; };

  parser->Init(init_cb, sample_cb, text_cb, nullptr);

  // Parse initial chunk to trigger init_cb
  (void)parser->Parse(buf.data(), static_cast<int>(bytes_read));

  // If not yet initialized, read more data up to ~4 MB
  size_t total_parsed = bytes_read;
  const size_t kMaxProbeSize = 4 * 1024 * 1024;
  while (!init_received && total_parsed < kMaxProbeSize) {
    bytes_read = file->Read(buf.data(), kBufSize);
    if (bytes_read <= 0) break;
    (void)parser->Parse(buf.data(), static_cast<int>(bytes_read));
    total_parsed += bytes_read;
  }

  (void)parser->Flush();
  file->Close();

  return init_received ? 0 : -5;
}
