/**
 * Shaka Decryptor - High-Level TypeScript / Bun SDK
 * ==================================================
 * 
 * Provides an idiomatic, object-oriented API for media decryption,
 * one-shot in-memory buffer processing, multi-track packaging, and media probing.
 */

import { dlopen, FFIType, ptr, toArrayBuffer } from "bun:ffi";
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
  streamSelector?: string;
  language?: string;
  trackLabel?: string;
  outputFormat?: string;
  inputFormat?: string;
  forcedSubtitle?: boolean;
  bandwidth?: number;
  trickPlayFactor?: number;
}

export interface ShakaStreamMetadata {
  streamType: "VIDEO" | "AUDIO" | "TEXT" | "UNKNOWN";
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

export interface ShakaStats {
  totalBytesRead: number;
  totalBytesWritten: number;
  executionDurationMs: number;
  throughputMBps: number;
}

export function b64ToHex(b64: string): string {
  const s = b64.replace(/-/g, "+").replace(/_/g, "/");
  const padded = s + "=".repeat((4 - (s.length % 4)) % 4);
  return Buffer.from(padded, "base64").toString("hex");
}

function findLibrary(customPath?: string): string {
  if (customPath && fs.existsSync(customPath)) return path.resolve(customPath);
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(scriptDir, "..", "..");
  const candidates = [
    path.join(repoRoot, "build", "Release", "shaka_decryptor.dll"),
    path.join(repoRoot, "build", "shaka_decryptor.dll"),
    path.join(repoRoot, "build", "libshaka_decryptor.so"),
    path.join(repoRoot, "build", "libshaka_decryptor.dylib"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  throw new Error("Could not find shaka_decryptor shared library.");
}

let _ffi: any = null;
function getFFI() {
  if (!_ffi) {
    const libPath = findLibrary();
    _ffi = dlopen(libPath, {
      ShakaDecryptor_Create: { args: [], returns: FFIType.ptr },
      ShakaDecryptor_Destroy: { args: [FFIType.ptr], returns: FFIType.void },
      ShakaDecryptor_GetLastError: { args: [FFIType.ptr], returns: FFIType.cstring },
      ShakaDecryptor_AddRawKey: { args: [FFIType.ptr, FFIType.cstring, FFIType.cstring], returns: FFIType.i32 },
      ShakaDecryptor_AddStream: { args: [FFIType.ptr, FFIType.cstring, FFIType.ptr, FFIType.ptr, FFIType.ptr, FFIType.cstring], returns: FFIType.i32 },
      ShakaDecryptor_AddStreamEx: { args: [FFIType.ptr, FFIType.cstring, FFIType.ptr, FFIType.ptr, FFIType.ptr, FFIType.cstring, FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_SetProgressCallback: { args: [FFIType.ptr, FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_SetLogCallback: { args: [FFIType.ptr, FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_SetLogLevel: { args: [FFIType.ptr, FFIType.i32], returns: FFIType.i32 },
      ShakaDecryptor_SetConsoleLogging: { args: [FFIType.i32], returns: FFIType.i32 },
      ShakaDecryptor_Run: { args: [FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_Cancel: { args: [FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_GetStats: { args: [FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_ProbeMedia: { args: [FFIType.cstring, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_DecryptBuffer: { args: [FFIType.ptr, FFIType.u64, FFIType.cstring, FFIType.cstring, FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
      ShakaDecryptor_FreeBuffer: { args: [FFIType.ptr], returns: FFIType.void },
    });
  }
  return _ffi.symbols;
}

/**
 * High-Level Decryptor Session
 */
export class ShakaDecryptorSession {
  private ctx: any;
  private symbols: any;
  private isDestroyed = false;

  constructor() {
    this.symbols = getFFI();
    this.ctx = this.symbols.ShakaDecryptor_Create();
  }

  public addKey(keyIdHexOrB64: string, keyHexOrB64: string): this {
    const kid = keyIdHexOrB64.length === 32 ? keyIdHexOrB64 : b64ToHex(keyIdHexOrB64);
    const key = keyHexOrB64.length === 32 ? keyHexOrB64 : b64ToHex(keyHexOrB64);
    this.symbols.ShakaDecryptor_AddRawKey(
      this.ctx,
      Buffer.from(kid + "\0"),
      Buffer.from(key + "\0")
    );
    return this;
  }

  public setLogLevel(level: ShakaLogLevel): this {
    this.symbols.ShakaDecryptor_SetLogLevel(this.ctx, level);
    return this;
  }

  public setConsoleLogging(enabled: boolean): this {
    this.symbols.ShakaDecryptor_SetConsoleLogging(enabled ? 1 : 0);
    return this;
  }

  public addFileStream(inputPath: string, outputPath: string): this {
    this.symbols.ShakaDecryptor_AddStream(
      this.ctx,
      Buffer.from(path.resolve(inputPath) + "\0"),
      null, null, null,
      Buffer.from(path.resolve(outputPath) + "\0")
    );
    return this;
  }

  public run(): void {
    const res = this.symbols.ShakaDecryptor_Run(this.ctx);
    if (res !== 0) {
      const err = this.symbols.ShakaDecryptor_GetLastError(this.ctx);
      throw new Error(`Decryption failed (code ${res}): ${err}`);
    }
  }

  public cancel(): void {
    this.symbols.ShakaDecryptor_Cancel(this.ctx);
  }

  public getStats(): ShakaStats {
    const buf = Buffer.alloc(32);
    this.symbols.ShakaDecryptor_GetStats(this.ctx, buf);
    return {
      totalBytesRead: Number(buf.readBigUInt64LE(0)),
      totalBytesWritten: Number(buf.readBigUInt64LE(8)),
      executionDurationMs: buf.readDoubleLE(16),
      throughputMBps: buf.readDoubleLE(24),
    };
  }

  public destroy(): void {
    if (!this.isDestroyed && this.ctx) {
      this.symbols.ShakaDecryptor_Destroy(this.ctx);
      this.isDestroyed = true;
    }
  }

  /**
   * One-Shot In-Memory Buffer Decryption
   */
  public static decryptBuffer(
    inputBuffer: Uint8Array | Buffer,
    keyIdHexOrB64: string,
    keyHexOrB64: string
  ): Uint8Array {
    const symbols = getFFI();
    const kid = keyIdHexOrB64.length === 32 ? keyIdHexOrB64 : b64ToHex(keyIdHexOrB64);
    const key = keyHexOrB64.length === 32 ? keyHexOrB64 : b64ToHex(keyHexOrB64);

    const outPtrBuf = Buffer.alloc(8);
    const outSizeBuf = Buffer.alloc(8);

    const inBuf = Buffer.isBuffer(inputBuffer) ? inputBuffer : Buffer.from(inputBuffer);

    const res = symbols.ShakaDecryptor_DecryptBuffer(
      inBuf,
      BigInt(inBuf.length),
      Buffer.from(kid + "\0"),
      Buffer.from(key + "\0"),
      outPtrBuf,
      outSizeBuf
    );

    if (res !== 0) {
      throw new Error(`One-shot buffer decryption failed with code: ${res}`);
    }

    const outPtr = outPtrBuf.readBigUInt64LE(0);
    const outSize = Number(outSizeBuf.readBigUInt64LE(0));

    if (outPtr === 0n || outSize === 0) {
      throw new Error("Decryption returned empty output buffer.");
    }

    const arrayBuf = toArrayBuffer(outPtr, 0, outSize);
    const copy = new Uint8Array(arrayBuf.slice(0));

    symbols.ShakaDecryptor_FreeBuffer(outPtr);
    return copy;
  }

  /**
   * Media File Probing
   */
  public static probe(filePath: string): ShakaMediaInfo {
    const symbols = getFFI();
    const probeBuf = Buffer.alloc(2048);
    const res = symbols.ShakaDecryptor_ProbeMedia(
      Buffer.from(path.resolve(filePath) + "\0"),
      probeBuf
    );

    if (res !== 0) {
      throw new Error(`Probe failed with code: ${res}`);
    }

    const streamCount = probeBuf.readInt32LE(0);
    const containerFmt = probeBuf.toString("utf8", 4 + 16 * 88, 4 + 16 * 88 + 32).replace(/\0/g, "");
    const duration = probeBuf.readDoubleLE(4 + 16 * 88 + 32);

    const streams: ShakaStreamMetadata[] = [];
    const structSize = 88;
    let offset = 8;

    for (let s = 0; s < streamCount; s++) {
      const sType = probeBuf.readInt32LE(offset);
      const codec = probeBuf.toString("utf8", offset + 4, offset + 36).replace(/\0/g, "");
      const lang = probeBuf.toString("utf8", offset + 36, offset + 52).replace(/\0/g, "");
      const width = probeBuf.readInt32LE(offset + 52);
      const height = probeBuf.readInt32LE(offset + 56);
      const fps = probeBuf.readDoubleLE(offset + 64);
      const channels = probeBuf.readInt32LE(offset + 72);
      const sampleRate = probeBuf.readInt32LE(offset + 76);
      const dur = probeBuf.readDoubleLE(offset + 80);

      streams.push({
        streamType: sType === 2 ? "VIDEO" : sType === 1 ? "AUDIO" : sType === 3 ? "TEXT" : "UNKNOWN",
        codec,
        language: lang || "und",
        width,
        height,
        frameRate: fps,
        audioChannels: channels,
        sampleRate,
        durationSeconds: dur,
      });
      offset += structSize;
    }

    return {
      streamCount,
      streams,
      containerFormat: containerFmt || "MP4/ISOBMFF",
      durationSeconds: duration,
    };
  }
}
