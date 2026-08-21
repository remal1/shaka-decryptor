/**
 * Shaka Decryptor - Background Worker Thread
 * ==========================================
 * 
 * Runs Shaka Decryptor operations on a dedicated OS worker thread
 * so the main JavaScript event loop remains completely non-blocking.
 */

import { loadShakaDecryptor, b64ToHex, ShakaLogLevel } from "./shaka_bindings";
import fs from "fs";

declare var self: Worker;

interface StreamItem {
  name: string;
  inputPath?: string;
  outputPath: string;
  inputData?: Uint8Array; // in-memory buffer
}

interface DecryptJob {
  id: string;
  kid: string; // Hex or Base64
  key: string; // Hex or Base64
  streams: StreamItem[];
  verbose?: boolean;
}

self.onmessage = async (event: MessageEvent<DecryptJob>) => {
  const workerStart = performance.now();
  const { id, kid, key, streams, verbose } = event.data;

  // Convert keys if base64
  const kidHex = kid.length === 32 ? kid : b64ToHex(kid);
  const keyHex = key.length === 32 ? key : b64ToHex(key);

  const t0 = performance.now();
  const { symbols, dllPath } = loadShakaDecryptor();
  const loadLibraryMs = performance.now() - t0;

  const t1 = performance.now();
  const ctx = symbols.ShakaDecryptor_Create();
  const createCtxMs = performance.now() - t1;

  if (!ctx) {
    self.postMessage({ id, success: false, error: "Failed to create ShakaDecryptor context." });
    return;
  }

  const tempFilesToClean: string[] = [];

  try {
    // Add raw key
    const t2 = performance.now();
    const retKey = symbols.ShakaDecryptor_AddRawKey(
      ctx,
      Buffer.from(`${kidHex}\0`),
      Buffer.from(`${keyHex}\0`)
    );
    const addKeyMs = performance.now() - t2;

    if (retKey !== 0) {
      const err = symbols.ShakaDecryptor_GetLastError(ctx);
      throw new Error(`Failed to add key: ${err ? err.toString() : "Unknown error"}`);
    }

    symbols.ShakaDecryptor_SetConsoleLogging(verbose ? 1 : 0);
    symbols.ShakaDecryptor_SetLogLevel(ctx, verbose ? ShakaLogLevel.INFO : ShakaLogLevel.ERROR);

    // Prepare streams
    const t3 = performance.now();
    for (const s of streams) {
      let inputPath = s.inputPath;

      // If in-memory data is provided, write to a fast local temporary file for the demuxer
      if (s.inputData) {
        const tmpPath = `temp_worker_in_${Date.now()}_${Math.random().toString(36).substring(2, 8)}.mp4`;
        await Bun.write(tmpPath, s.inputData);
        inputPath = tmpPath;
        tempFilesToClean.push(tmpPath);
      }

      if (!inputPath) {
        throw new Error(`No input path or buffer provided for stream ${s.name}`);
      }

      const retStream = symbols.ShakaDecryptor_AddStream(
        ctx,
        Buffer.from(`${inputPath}\0`),
        null,
        null,
        null,
        Buffer.from(`${s.outputPath}\0`)
      );

      if (retStream !== 0) {
        const err = symbols.ShakaDecryptor_GetLastError(ctx);
        throw new Error(`AddStream failed: ${err ? err.toString() : "Unknown error"}`);
      }
    }
    const addStreamMs = performance.now() - t3;

    // Run decryption (pure C++ execution)
    const t4 = performance.now();
    const runRes = symbols.ShakaDecryptor_Run(ctx);
    const decryptRunMs = performance.now() - t4;

    if (runRes !== 0) {
      const err = symbols.ShakaDecryptor_GetLastError(ctx);
      throw new Error(`Decryption failed with code ${runRes}: ${err ? err.toString() : "Unknown"}`);
    }

    const t5 = performance.now();
    symbols.ShakaDecryptor_Destroy(ctx);
    const destroyCtxMs = performance.now() - t5;

    // Success response
    const results = streams.map((s) => ({
      name: s.name,
      outputPath: s.outputPath,
      outputSize: fs.existsSync(s.outputPath) ? fs.statSync(s.outputPath).size : 0,
    }));

    const totalWorkerMs = performance.now() - workerStart;

    self.postMessage({
      id,
      success: true,
      results,
      dllPath,
      timings: {
        loadLibraryMs: +loadLibraryMs.toFixed(3),
        createCtxMs: +createCtxMs.toFixed(3),
        addKeyMs: +addKeyMs.toFixed(3),
        addStreamMs: +addStreamMs.toFixed(3),
        decryptRunMs: +decryptRunMs.toFixed(3),
        destroyCtxMs: +destroyCtxMs.toFixed(3),
        totalWorkerMs: +totalWorkerMs.toFixed(3),
      },
    });

  } catch (err: any) {
    symbols.ShakaDecryptor_Destroy(ctx);
    self.postMessage({ id, success: false, error: err.message || String(err) });
  } finally {
    for (const tmp of tempFilesToClean) {
      if (fs.existsSync(tmp)) {
        fs.unlinkSync(tmp);
      }
    }
  }
};
