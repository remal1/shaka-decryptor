/**
 * In-Memory Buffer Decryption in Bun (Worker Thread)
 * ==================================================
 * 
 * Demonstrates downloading DASH segments directly into memory (Uint8Array)
 * and passing the buffer to a background Worker thread for decryption,
 * with precise per-step timing benchmarks.
 * 
 * Run with:
 *   bun examples/bun/in_memory_decrypt_demo.ts
 */

import path from "path";
import fs from "fs";

export async function decryptMemoryBufferAsync(params: {
  kid: string;
  key: string;
  name: string;
  buffer: Uint8Array;
  outputPath: string;
  verbose?: boolean;
}): Promise<any> {
  const workerPath = path.resolve(__dirname, "decrypt_worker.ts");
  const t0 = performance.now();
  const worker = new Worker(new URL(workerPath, import.meta.url).href);

  return new Promise((resolve, reject) => {
    const jobId = `job_mem_${Date.now()}`;

    worker.onmessage = (event: MessageEvent) => {
      const { id, success, results, timings, error } = event.data;
      if (id !== jobId) return;

      const workerRoundtripMs = performance.now() - t0;
      worker.terminate();
      if (success) {
        resolve({ results, timings, workerRoundtripMs });
      } else {
        reject(new Error(error));
      }
    };

    worker.onerror = (err) => {
      worker.terminate();
      reject(err);
    };

    worker.postMessage({
      id: jobId,
      kid: params.kid,
      key: params.key,
      streams: [
        {
          name: params.name,
          outputPath: path.resolve(params.outputPath),
          inputData: params.buffer,
        },
      ],
      verbose: params.verbose,
    });
  });
}

async function main() {
  const globalStart = performance.now();

  const baseUrl = "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const repId = "5"; // 1080p
  const numSegments = 3;
  const kid = "nrQFDeRLSAKTLifXUIPiZg";
  const key = "FmY0xnWCPCNaSpRG-tUuTQ";
  const outputPath = "bun_in_memory_1080p.mp4";

  console.log("===================================================================");
  console.log("     IN-MEMORY BUFFER DECRYPTION & STEP BENCHMARK (BUN)            ");
  console.log("===================================================================");
  console.log(`  Representation: ${repId} (1080p)`);
  console.log(`  Segments      : ${numSegments}`);
  console.log(`  Output Path   : ${outputPath}`);

  const stepTimings: { step: string; ms: number; note: string }[] = [];

  // 1. Download directly to RAM using fetch()
  console.log("\n--- [1. Downloading Segments directly into RAM] ---");
  const chunks: Uint8Array[] = [];

  const initUrl = `${baseUrl}/${repId}/init.mp4`;
  const tInit0 = performance.now();
  const initResp = await fetch(initUrl);
  const initBuf = new Uint8Array(await initResp.arrayBuffer());
  const initMs = performance.now() - tInit0;
  chunks.push(initBuf);
  stepTimings.push({
    step: "Download init.mp4 (Header)",
    ms: +initMs.toFixed(2),
    note: `${initBuf.length.toLocaleString()} bytes (${((initBuf.length / 1024 / 1024) / (initMs / 1000)).toFixed(2)} MB/s)`,
  });
  console.log(`  [fetch] init.mp4 : ${initBuf.length.toLocaleString().padStart(9)} bytes in ${initMs.toFixed(1).padStart(6)} ms`);

  for (let i = 1; i <= numSegments; i++) {
    const segNum = String(i).padStart(4, "0");
    const segUrl = `${baseUrl}/${repId}/${segNum}.m4s`;
    const tSeg0 = performance.now();
    const segResp = await fetch(segUrl);
    const segBuf = new Uint8Array(await segResp.arrayBuffer());
    const segMs = performance.now() - tSeg0;
    chunks.push(segBuf);
    stepTimings.push({
      step: `Download segment ${segNum}.m4s`,
      ms: +segMs.toFixed(2),
      note: `${segBuf.length.toLocaleString()} bytes (${((segBuf.length / 1024 / 1024) / (segMs / 1000)).toFixed(2)} MB/s)`,
    });
    console.log(`  [fetch] ${segNum}.m4s : ${segBuf.length.toLocaleString().padStart(9)} bytes in ${segMs.toFixed(1).padStart(6)} ms`);
  }

  // Buffer Assembly
  const tAssemble0 = performance.now();
  const totalLength = chunks.reduce((acc, c) => acc + c.length, 0);
  const memoryBuffer = new Uint8Array(totalLength);
  let pos = 0;
  for (const c of chunks) {
    memoryBuffer.set(c, pos);
    pos += c.length;
  }
  const assembleMs = performance.now() - tAssemble0;
  stepTimings.push({
    step: "In-Memory Buffer Assembly",
    ms: +assembleMs.toFixed(2),
    note: `${(totalLength / 1024 / 1024).toFixed(2)} MB combined in RAM`,
  });

  console.log(`\n[+] In-memory buffer assembled: ${memoryBuffer.length.toLocaleString()} bytes in RAM`);

  // 2. Dispatch to Background Worker
  console.log("\n--- [2. Offloading Decryption to Background Worker Thread] ---");
  const response = await decryptMemoryBufferAsync({
    kid,
    key,
    name: `dash_1080p_rep_${repId}`,
    buffer: memoryBuffer,
    outputPath,
    verbose: false,
  });

  const globalElapsed = performance.now() - globalStart;
  const { results, timings, workerRoundtripMs } = response;
  const decryptSpeed = (totalLength / 1024 / 1024) / (timings.decryptRunMs / 1000);

  console.log("\n===================================================================");
  console.log("                  DETAILED STEP BENCHMARKS                         ");
  console.log("===================================================================");
  console.log(" Step Description                           Duration       Details");
  console.log("-------------------------------------------------------------------");
  for (const s of stepTimings) {
    console.log(` ${s.step.padEnd(38)} : ${`${s.ms.toFixed(2)} ms`.padStart(11)} | ${s.note}`);
  }
  console.log("-------------------------------------------------------------------");
  console.log(` C++ Worker Thread Breakdown:`);
  console.log(`   - Load DLL & Bindings                   : ${timings.loadLibraryMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_Create                 : ${timings.createCtxMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_AddRawKey              : ${timings.addKeyMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_AddStream              : ${timings.addStreamMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_Run (Pure Decrypt+Mux) : ${timings.decryptRunMs.toFixed(2).padStart(8)} ms | ${decryptSpeed.toFixed(2)} MB/s`);
  console.log(`   - ShakaDecryptor_Destroy                : ${timings.destroyCtxMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - Worker Roundtrip (IPC + Decrypt)      : ${workerRoundtripMs.toFixed(2).padStart(8)} ms`);
  console.log("-------------------------------------------------------------------");
  console.log(` Total Pipeline Time (End-to-End)          : ${globalElapsed.toFixed(2).padStart(8)} ms (${(globalElapsed / 1000).toFixed(2)} s)`);
  console.log(` Total Data Processed                      : ${(totalLength / 1024 / 1024).toFixed(2)} MB (${totalLength.toLocaleString()} bytes)`);
  console.log(` Pure Decryption Throughput                : ${decryptSpeed.toFixed(2)} MB/s (${(decryptSpeed * 8).toFixed(1)} Mbps)`);
  console.log(` Decrypted Output File                     : ${path.resolve(outputPath)}`);
  console.log("===================================================================\n");
}

await main();
