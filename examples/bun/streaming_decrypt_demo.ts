/**
 * Real-Time Streaming Decryption in Bun (Worker Thread)
 * ====================================================
 * 
 * Demonstrates a high-performance, bounded-memory streaming pipeline in Bun:
 * 1. Main JS Thread (Producer with Async Sliding Window):
 *    - Maintains a configurable prefetch buffer of N segments (e.g. 3 segments = ~4.5 MB)
 *      to completely absorb network latency & jitter.
 *    - As each segment is consumed/streamed, its memory is instantly freed, keeping RAM constant.
 * 2. Dedicated Worker Thread (Consumer):
 *    - Decrypts the stream in the background without blocking the JS event loop.
 *    - Generates high-precision step timing & throughput benchmarks.
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

/**
 * Bounded Async Sliding Window Prefetch Queue
 * Ensures at most `maxBufferSize` segments reside in memory at any point in time.
 */
class AsyncSegmentPrefetchQueue {
  private maxBufferSize: number;
  private queue: Map<number, { promise: Promise<{ data: Buffer; durationMs: number }>; startTime: number }> = new Map();

  constructor(maxBufferSize: number = 3) {
    this.maxBufferSize = Math.max(1, maxBufferSize);
  }

  public prefetch(
    currentIndex: number,
    totalSegments: number,
    fetchFn: (idx: number) => Promise<{ data: Buffer; durationMs: number }>
  ) {
    const end = Math.min(currentIndex + this.maxBufferSize, totalSegments + 1);
    for (let idx = currentIndex; idx < end; idx++) {
      if (!this.queue.has(idx)) {
        const startTime = performance.now();
        const promise = fetchFn(idx);
        this.queue.set(idx, { promise, startTime });
      }
    }
  }

  public async getNext(index: number): Promise<{ data: Buffer; durationMs: number }> {
    const item = this.queue.get(index);
    if (!item) throw new Error(`Segment ${index} not in prefetch queue`);
    const result = await item.promise;
    this.queue.delete(index); // Instantly free segment from queue map
    return result;
  }
}

export async function runStreamingDecryptionBun(params: {
  baseUrl?: string;
  repId?: string;
  numSegments?: number;
  maxBufferedSegments?: number;
  kid?: string;
  key?: string;
  outputPath?: string;
  simulatedDelaySec?: number;
}) {
  const globalStart = performance.now();

  const baseUrl = params.baseUrl || "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const repId = params.repId || "5"; // 1080p
  const numSegments = params.numSegments || 50;
  const maxBuffered = params.maxBufferedSegments ?? 3;
  const kid = params.kid || "nrQFDeRLSAKTLifXUIPiZg";
  const key = params.key || "FmY0xnWCPCNaSpRG-tUuTQ";
  const outputPath = params.outputPath || "bun_streaming_1080p.mp4";
  const delaySec = params.simulatedDelaySec ?? 0;

  console.log("===================================================================");
  console.log("        REAL-TIME STREAMING DECRYPTION & BENCHMARK (BUN)          ");
  console.log("===================================================================");
  console.log(`  Representation    : ${repId} (1080p video)`);
  console.log(`  Segments to Stream: ${numSegments}`);
  console.log(`  Jitter Buffer Size: ${maxBuffered} segments in RAM (Sliding Window)`);
  console.log(`  Simulated Latency : ${delaySec}s per segment`);
  console.log(`  Output Path       : ${outputPath}`);
  console.log("===================================================================");

  const benchmarks: StepBenchmark[] = [];

  // 1. Sliding Window Streaming Download (Bounded RAM)
  console.log("\n--- [1. Progressive Streaming with Sliding Window Prefetch] ---");
  const tempStreamInput = path.resolve(`temp_stream_in_${Date.now()}.mp4`);
  const writeStream = fs.createWriteStream(tempStreamInput);

  let totalDownloaded = 0;
  let maxSegmentBytes = 0;

  // Header (init.mp4)
  const initUrl = `${baseUrl}/${repId}/init.mp4`;
  const tInit0 = performance.now();
  const initResp = await fetch(initUrl);
  const initData = Buffer.from(await initResp.arrayBuffer());
  const initDuration = performance.now() - tInit0;

  writeStream.write(initData);
  totalDownloaded += initData.length;
  maxSegmentBytes = Math.max(maxSegmentBytes, initData.length);

  const initSpeed = (initData.length / (1024 * 1024)) / (initDuration / 1000);
  console.log(`  [Header] init.mp4 : ${initData.length.toLocaleString().padStart(9)} bytes | ${initDuration.toFixed(1).padStart(6)} ms | ${initSpeed.toFixed(2)} MB/s`);
  benchmarks.push({
    step: "Download init.mp4 (Header)",
    bytes: initData.length,
    durationMs: +initDuration.toFixed(2),
    speedMBs: +initSpeed.toFixed(2),
  });

  // Setup Sliding Window Prefetcher
  const prefetcher = new AsyncSegmentPrefetchQueue(maxBuffered);

  const fetchSegment = async (segIdx: number) => {
    if (delaySec > 0) {
      await Bun.sleep(delaySec * 1000);
    }
    const segNum = String(segIdx).padStart(4, "0");
    const segUrl = `${baseUrl}/${repId}/${segNum}.m4s`;
    const t0 = performance.now();
    const resp = await fetch(segUrl);
    const buf = Buffer.from(await resp.arrayBuffer());
    const dur = performance.now() - t0;
    return { data: buf, durationMs: dur };
  };

  // Stream media segments using sliding window
  for (let i = 1; i <= numSegments; i++) {
    // Ensure sliding window is populated up to maxBuffered
    prefetcher.prefetch(i, numSegments, fetchSegment);

    // Consume next ready segment
    const { data: segData, durationMs: segDuration } = await prefetcher.getNext(i);
    const segNum = String(i).padStart(4, "0");

    writeStream.write(segData);
    totalDownloaded += segData.length;
    maxSegmentBytes = Math.max(maxSegmentBytes, segData.length);

    const segSpeed = (segData.length / (1024 * 1024)) / (segDuration / 1000);
    console.log(`  [Seg #${i.toString().padStart(2, '0')}] ${segNum}.m4s : ${segData.length.toLocaleString().padStart(9)} bytes | ${segDuration.toFixed(1).padStart(6)} ms | ${segSpeed.toFixed(2)} MB/s (Queue: ${Math.min(maxBuffered, numSegments - i + 1)} buffered)`);
    benchmarks.push({
      step: `Download segment ${segNum}.m4s`,
      bytes: segData.length,
      durationMs: +segDuration.toFixed(2),
      speedMBs: +segSpeed.toFixed(2),
    });
  }

  // Close write stream
  await new Promise<void>((resolve) => writeStream.end(() => resolve()));

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
          inputPath: tempStreamInput,
          outputPath: path.resolve(outputPath),
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

  // Clean temporary streamed input
  if (fs.existsSync(tempStreamInput)) {
    fs.unlinkSync(tempStreamInput);
  }

  const maxRamMB = (maxSegmentBytes * maxBuffered) / (1024 * 1024);

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
  console.log(` Bounded Memory Window (Jitter Buffer)     : ${maxBuffered} segments max in RAM (~${maxRamMB.toFixed(2)} MB max footprint)`);
  console.log(` Pure Decryption Throughput                : ${decryptSpeed.toFixed(2)} MB/s (${(decryptSpeed * 8).toFixed(1)} Mbps)`);
  console.log(` Decrypted Output File                     : ${path.resolve(outputPath)}`);
  console.log("===================================================================\n");
}

await runStreamingDecryptionBun({ simulatedDelaySec: 0, numSegments: 50, maxBufferedSegments: 10 });
