/**
 * Real-Time Streaming Decryption in Bun (Worker Thread)
 * ====================================================
 * 
 * Demonstrates a real-time streaming pipeline in Bun with detailed per-step microsecond/millisecond benchmarks:
 * 1. Main JS Thread (Producer):
 *    - Downloads DASH segments (init.mp4 + media segments) progressively with fetch()
 *    - Measures exact download latency & bandwidth per segment.
 * 2. Dedicated Worker Thread (Consumer):
 *    - Decrypts segments in the background without blocking the JS event loop.
 *    - Reports granular C++ execution times (Context Create, AddKey, Pure Decrypt, Destroy).
 * 
 * Run with:
 *   bun examples/bun/streaming_decrypt_demo.ts
 */

import path from "path";
import fs from "fs";

interface StepBenchmark {
  step: string;
  bytes?: number;
  durationMs: number;
  speedMBs?: number;
}

export async function runStreamingDecryptionBun(params: {
  baseUrl?: string;
  repId?: string;
  numSegments?: number;
  kid?: string;
  key?: string;
  outputPath?: string;
  simulatedDelaySec?: number;
}) {
  const globalStart = performance.now();

  const baseUrl = params.baseUrl || "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const repId = params.repId || "5"; // 1080p
  const numSegments = params.numSegments || 4;
  const kid = params.kid || "nrQFDeRLSAKTLifXUIPiZg";
  const key = params.key || "FmY0xnWCPCNaSpRG-tUuTQ";
  const outputPath = params.outputPath || "bun_streaming_1080p.mp4";
  const delaySec = params.simulatedDelaySec ?? 0;

  console.log("===================================================================");
  console.log("        REAL-TIME STREAMING DECRYPTION & BENCHMARK (BUN)          ");
  console.log("===================================================================");
  console.log(`  Representation    : ${repId} (1080p video)`);
  console.log(`  Segments to Stream: ${numSegments}`);
  console.log(`  Simulated Latency : ${delaySec}s per segment`);
  console.log(`  Output Path       : ${outputPath}`);

  const benchmarks: StepBenchmark[] = [];

  // 1. Download chunks progressively
  console.log("\n--- [1. Network Download Pipeline (Main JS Thread)] ---");
  const downloadStart = performance.now();
  const chunks: Uint8Array[] = [];
  let totalDownloaded = 0;

  // Header
  const initUrl = `${baseUrl}/${repId}/init.mp4`;
  const tInit0 = performance.now();
  const initResp = await fetch(initUrl);
  const initData = new Uint8Array(await initResp.arrayBuffer());
  const initDuration = performance.now() - tInit0;
  chunks.push(initData);
  totalDownloaded += initData.length;

  const initSpeed = (initData.length / (1024 * 1024)) / (initDuration / 1000);
  console.log(`  [Header] init.mp4 : ${initData.length.toLocaleString().padStart(9)} bytes | ${initDuration.toFixed(1).padStart(6)} ms | ${initSpeed.toFixed(2)} MB/s`);
  benchmarks.push({
    step: "Download init.mp4 (Header)",
    bytes: initData.length,
    durationMs: +initDuration.toFixed(2),
    speedMBs: +initSpeed.toFixed(2),
  });

  // Media segments
  for (let i = 1; i <= numSegments; i++) {
    if (delaySec > 0) {
      await Bun.sleep(delaySec * 1000);
    }
    const segNum = String(i).padStart(4, "0");
    const segUrl = `${baseUrl}/${repId}/${segNum}.m4s`;

    const tSeg0 = performance.now();
    const segResp = await fetch(segUrl);
    const segData = new Uint8Array(await segResp.arrayBuffer());
    const segDuration = performance.now() - tSeg0;
    chunks.push(segData);
    totalDownloaded += segData.length;

    const segSpeed = (segData.length / (1024 * 1024)) / (segDuration / 1000);
    console.log(`  [Seg #${i}] ${segNum}.m4s : ${segData.length.toLocaleString().padStart(9)} bytes | ${segDuration.toFixed(1).padStart(6)} ms | ${segSpeed.toFixed(2)} MB/s`);
    benchmarks.push({
      step: `Download segment ${segNum}.m4s`,
      bytes: segData.length,
      durationMs: +segDuration.toFixed(2),
      speedMBs: +segSpeed.toFixed(2),
    });
  }

  const totalDownloadTime = performance.now() - downloadStart;

  // Buffer assembly
  const tAssemble0 = performance.now();
  const combinedData = new Uint8Array(totalDownloaded);
  let offset = 0;
  for (const c of chunks) {
    combinedData.set(c, offset);
    offset += c.length;
  }
  const assembleDuration = performance.now() - tAssemble0;
  benchmarks.push({
    step: "In-Memory Buffer Assembly",
    bytes: totalDownloaded,
    durationMs: +assembleDuration.toFixed(2),
  });

  // 2. Offload to Background Worker
  console.log("\n--- [2. Decryption Pipeline (Background Worker Thread)] ---");
  const workerPath = path.resolve(__dirname, "decrypt_worker.ts");
  const tWorkerSpawn0 = performance.now();
  const worker = new Worker(new URL(workerPath, import.meta.url).href);

  const workerResponse: any = await new Promise((resolve, reject) => {
    const jobId = `job_stream_${Date.now()}`;

    worker.onmessage = (e: MessageEvent) => {
      const { id, success, results, timings, error } = e.data;
      if (id !== jobId) return;
      worker.terminate();
      if (success) resolve({ results, timings });
      else reject(new Error(error));
    };

    worker.onerror = (err) => {
      worker.terminate();
      reject(err);
    };

    worker.postMessage({
      id: jobId,
      kid,
      key,
      streams: [
        {
          name: `dash_stream_rep_${repId}`,
          outputPath: path.resolve(outputPath),
          inputData: combinedData,
        },
      ],
      verbose: false,
    });
  });

  const totalWorkerE2E = performance.now() - tWorkerSpawn0;
  const globalElapsed = performance.now() - globalStart;

  const { results, timings } = workerResponse;
  const pureDecryptTime = timings.decryptRunMs;
  const decryptSpeed = (totalDownloaded / (1024 * 1024)) / (pureDecryptTime / 1000);

  // 3. Print Detailed Step Timing Table
  console.log("\n===================================================================");
  console.log("                  DETAILED STEP BENCHMARKS                         ");
  console.log("===================================================================");
  console.log(" Step Description                           Duration       Speed / Notes");
  console.log("-------------------------------------------------------------------");
  for (const b of benchmarks) {
    const desc = b.step.padEnd(38);
    const dur = `${b.durationMs.toFixed(2)} ms`.padStart(11);
    const note = b.speedMBs ? `${b.speedMBs.toFixed(2)} MB/s` : `${(b.bytes! / 1024).toFixed(1)} KB`;
    console.log(` ${desc} : ${dur} | ${note}`);
  }
  console.log("-------------------------------------------------------------------");
  console.log(` C++ Worker Thread Breakdown:`);
  console.log(`   - Load DLL & Bindings                   : ${timings.loadLibraryMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_Create                 : ${timings.createCtxMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_AddRawKey              : ${timings.addKeyMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_AddStream              : ${timings.addStreamMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - ShakaDecryptor_Run (Pure Decrypt+Mux) : ${timings.decryptRunMs.toFixed(2).padStart(8)} ms | ${decryptSpeed.toFixed(2)} MB/s`);
  console.log(`   - ShakaDecryptor_Destroy                : ${timings.destroyCtxMs.toFixed(2).padStart(8)} ms`);
  console.log(`   - Total Worker Thread E2E               : ${totalWorkerE2E.toFixed(2).padStart(8)} ms`);
  console.log("-------------------------------------------------------------------");
  console.log(` Total Pipeline Time (End-to-End)          : ${globalElapsed.toFixed(2).padStart(8)} ms (${(globalElapsed / 1000).toFixed(2)} s)`);
  console.log(` Total Data Streamed                       : ${(totalDownloaded / (1024 * 1024)).toFixed(2)} MB (${totalDownloaded.toLocaleString()} bytes)`);
  console.log(` Pure Decryption Throughput                : ${decryptSpeed.toFixed(2)} MB/s (${(decryptSpeed * 8).toFixed(1)} Mbps)`);
  console.log(` Decrypted Output File                     : ${path.resolve(outputPath)}`);
  console.log("===================================================================\n");
}

await runStreamingDecryptionBun({ simulatedDelaySec: 0, numSegments: 50 });
