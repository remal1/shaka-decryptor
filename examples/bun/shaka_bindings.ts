/**
 * Shaka Decryptor - Bun FFI Bindings
 * ==================================
 */

import { dlopen, FFIType } from "bun:ffi";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "node:url";

export enum ShakaLogLevel {
  INFO = 0,
  WARNING = 1,
  ERROR = 2,
  FATAL = 3,
  NONE = 4,
}

export interface ShakaStreamOptions {
  streamSelector?: string; // "video", "audio", "text", "0", "1"
  language?: string;       // ISO-639-2 tag e.g. "hun", "eng", "fra"
  trackLabel?: string;     // e.g. "Magyar 5.1 Szinkron", "English Audio"
  outputFormat?: string;   // "mp4", "mkv", "webm", "ts"
  inputFormat?: string;    // "mp4", "webm", "vtt", "ttml"
  forcedSubtitle?: boolean;
  bandwidth?: number;
  trickPlayFactor?: number;
}

export interface ShakaStreamMetadata {
  streamType: number;      // 0=Unknown, 1=Video, 2=Audio, 3=Text
  codec: string;
  language: string;
  width: number;
  height: number;
  frameRate: number;
  audioChannels: number;
  sampleRate: number;
  durationSeconds: number;
}

export interface ShakaMediaInfo {
  streamCount: number;
  streams: ShakaStreamMetadata[];
  containerFormat: string;
  durationSeconds: number;
}

export function b64ToHex(b64: string): string {
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const binary = Buffer.from(padded, "base64");
  return binary.toString("hex");
}

export function findLibraryPath(customPath?: string): string {
  if (customPath && fs.existsSync(customPath)) return path.resolve(customPath);

  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(scriptDir, "..", "..");

  const candidates = [
    path.join(repoRoot, "build", "Release", "shaka_decryptor.dll"),
    path.join(repoRoot, "build", "shaka_decryptor.dll"),
    path.join(repoRoot, "build", "libshaka_decryptor.so"),
    path.join(repoRoot, "build", "libshaka_decryptor.dylib"),
    path.resolve("build/Release/shaka_decryptor.dll"),
    path.resolve("build/shaka_decryptor.dll"),
  ];

  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }

  throw new Error("Could not find shaka_decryptor library. Please build the project first.");
}

export function loadShakaDecryptor(customPath?: string) {
  const dllPath = findLibraryPath(customPath);

  const { symbols } = dlopen(dllPath, {
    ShakaDecryptor_Create: {
      args: [],
      returns: FFIType.ptr,
    },
    ShakaDecryptor_Destroy: {
      args: [FFIType.ptr],
      returns: FFIType.void,
    },
    ShakaDecryptor_GetLastError: {
      args: [FFIType.ptr],
      returns: FFIType.cstring,
    },
    ShakaDecryptor_AddRawKey: {
      args: [FFIType.ptr, FFIType.cstring, FFIType.cstring],
      returns: FFIType.i32,
    },
    ShakaDecryptor_AddStream: {
      args: [
        FFIType.ptr,      // ctx
        FFIType.cstring,  // name / input path
        FFIType.ptr,      // read_cb (null for file)
        FFIType.ptr,      // size_cb (null for file)
        FFIType.ptr,      // stream_user_data
        FFIType.cstring,  // output path
      ],
      returns: FFIType.i32,
    },
    ShakaDecryptor_AddStreamEx: {
      args: [
        FFIType.ptr,      // ctx
        FFIType.cstring,  // name
        FFIType.ptr,      // read_cb
        FFIType.ptr,      // size_cb
        FFIType.ptr,      // stream_user_data
        FFIType.cstring,  // output path
        FFIType.ptr,      // write_cb
        FFIType.ptr,      // write_user_data
      ],
      returns: FFIType.i32,
    },
    ShakaDecryptor_AddStreamWithOptions: {
      args: [
        FFIType.ptr,      // ctx
        FFIType.cstring,  // name
        FFIType.ptr,      // read_cb
        FFIType.ptr,      // size_cb
        FFIType.ptr,      // stream_user_data
        FFIType.cstring,  // output path
        FFIType.ptr,      // write_cb
        FFIType.ptr,      // write_user_data
        FFIType.ptr,      // options pointer
      ],
      returns: FFIType.i32,
    },
    ShakaDecryptor_SetProgressCallback: {
      args: [FFIType.ptr, FFIType.ptr, FFIType.ptr],
      returns: FFIType.i32,
    },
    ShakaDecryptor_SetLogCallback: {
      args: [FFIType.ptr, FFIType.ptr, FFIType.ptr],
      returns: FFIType.i32,
    },
    ShakaDecryptor_SetLogLevel: {
      args: [FFIType.ptr, FFIType.i32],
      returns: FFIType.i32,
    },
    ShakaDecryptor_SetConsoleLogging: {
      args: [FFIType.i32],
      returns: FFIType.i32,
    },
    ShakaDecryptor_Run: {
      args: [FFIType.ptr],
      returns: FFIType.i32,
    },
    ShakaDecryptor_Cancel: {
      args: [FFIType.ptr],
      returns: FFIType.i32,
    },
    ShakaDecryptor_ProbeMedia: {
      args: [FFIType.cstring, FFIType.ptr],
      returns: FFIType.i32,
    },
  });

  return { symbols, dllPath };
}
