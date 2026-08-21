/**
 * Multi-Track & Multi-Stream Concurrent Decryption in Bun
 * =======================================================
 * 
 * Demonstrates downloading and decrypting multiple tracks (different video resolutions
 * and audio languages) concurrently using Shaka Packager's multi-threaded C++ engine.
 * 
 * Includes per-track sliding window prefetching (Jitter Buffer) to control the exact
 * number of segments in RAM per track.
 * 
 * Tracks in this demo:
 * 1. Video 1080p (Rep ID: 5)
 * 2. Video 720p  (Rep ID: 4)
 * 3. Audio EN    (Rep ID: 15)
 * 4. Audio AU    (Rep ID: 16)
 * 
 * Run with:
 *   bun examples/bun/multi_stream_decrypt_demo.ts
 */

import path from "path";
import fs from "fs";

interface TrackConfig {
  name: string;
  type: "video" | "audio";
  repId: string;
  numSegments: number;
  outputFile: string;
}

/**
 * Bounded Async Sliding Window Prefetch Queue per track
 */
class AsyncTrackPrefetchQueue {
  private maxBufferSize: number;
  private queue: Map<number, Promise<Buffer>> = new Map();

  constructor(maxBufferSize: number = 3) {
    this.maxBufferSize = Math.max(1, maxBufferSize);
  }

  public prefetch(
    currentIndex: number,
    totalSegments: number,
    fetchFn: (idx: number) => Promise<Buffer>
  ) {
    const end = Math.min(currentIndex + this.maxBufferSize, totalSegments + 1);
    for (let idx = currentIndex; idx < end; idx++) {
      if (!this.queue.has(idx)) {
        this.queue.set(idx, fetchFn(idx));
      }
    }
  }

  public async getNext(index: number): Promise<Buffer> {
    const promise = this.queue.get(index);
    if (!promise) throw new Error(`Segment ${index} not in prefetch queue`);
    const data = await promise;
    this.queue.delete(index); // Free segment memory from queue map immediately
    return data;
  }
}

export async function runMultiStreamDecryption(params?: {
  baseUrl?: string;
  numSegments?: number;
  maxBufferedSegments?: number;
  kid?: string;
  key?: string;
}) {
  const globalStart = performance.now();

  const baseUrl = params?.baseUrl || "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const numSegments = params?.numSegments || 10;
  const maxBuffered = params?.maxBufferedSegments ?? 3;
  const kid = params?.kid || "nrQFDeRLSAKTLifXUIPiZg";
  const key = params?.key || "FmY0xnWCPCNaSpRG-tUuTQ";

  const tracks: TrackConfig[] = [
    { name: "Video_1080p", type: "video", repId: "5", numSegments, outputFile: "out_video_1080p.mp4" },
    { name: "Video_720p", type: "video", repId: "4", numSegments, outputFile: "out_video_720p.mp4" },
    { name: "Audio_EN", type: "audio", repId: "15", numSegments, outputFile: "out_audio_en.mp4" },
    { name: "Audio_AU", type: "audio", repId: "17", numSegments, outputFile: "out_audio_au.mp4" },
  ];

  console.log("===================================================================");
  console.log("     MULTI-TRACK CONCURRENT DECRYPTION DEMO (BUN + SHAKA)          ");
  console.log("===================================================================");
  console.log(`  Total Tracks to Process : ${tracks.length}`);
  console.log(`  Segments per Track      : ${numSegments}`);
  console.log(`  Jitter Buffer per Track : ${maxBuffered} segments max in RAM`);
  console.log(`  KID                     : ${kid}`);
  console.log(`  KEY                     : ${key}`);
  console.log("===================================================================");

  // -------------------------------------------------------------------------
  // 1. Download all tracks concurrently in parallel using Promise.all
  // -------------------------------------------------------------------------
  console.log("\n--- [1. Concurrent Network Download Phase (All Tracks in Parallel)] ---");
  const tDownloadAll0 = performance.now();

  const downloadTrack = async (track: TrackConfig) => {
    const t0 = performance.now();
    const tempPath = path.resolve(`temp_multi_in_${track.name}_${Date.now()}.mp4`);
    const writeStream = fs.createWriteStream(tempPath);
    let trackBytes = 0;

    // 1. Fetch init.mp4
    const initUrl = `${baseUrl}/${track.repId}/init.mp4`;
    const initResp = await fetch(initUrl);
    const initBuf = Buffer.from(await initResp.arrayBuffer());
    writeStream.write(initBuf);
    trackBytes += initBuf.length;

    // 2. Fetch segments using Sliding Window Prefetch Queue
    const prefetcher = new AsyncTrackPrefetchQueue(maxBuffered);

    const fetchSingleSegment = async (segIdx: number) => {
      const segNum = String(segIdx).padStart(4, "0");
      const segUrl = `${baseUrl}/${track.repId}/${segNum}.m4s`;
      const resp = await fetch(segUrl);
      return Buffer.from(await resp.arrayBuffer());
    };

    for (let i = 1; i <= track.numSegments; i++) {
      prefetcher.prefetch(i, track.numSegments, fetchSingleSegment);
      const segBuf = await prefetcher.getNext(i);
      writeStream.write(segBuf);
      trackBytes += segBuf.length;
    }

    await new Promise<void>((resolve) => writeStream.end(() => resolve()));
    const durMs = performance.now() - t0;
    const speedMB = (trackBytes / 1024 / 1024) / (durMs / 1000);

    console.log(`  [Done] ${track.name.padEnd(12)} (Rep ${track.repId.padStart(2)}): ${trackBytes.toLocaleString().padStart(10)} bytes in ${durMs.toFixed(1).padStart(6)} ms (${speedMB.toFixed(2)} MB/s)`);

    return { track, inputPath: tempPath, bytes: trackBytes, downloadMs: durMs };
  };

  const downloadResults = await Promise.all(tracks.map(downloadTrack));
  const totalDownloadWallTime = performance.now() - tDownloadAll0;
  const totalDownloadedBytes = downloadResults.reduce((acc, r) => acc + r.bytes, 0);

  console.log(`\n[+] Parallel download finished in ${totalDownloadWallTime.toFixed(1)} ms (Total: ${(totalDownloadedBytes / 1024 / 1024).toFixed(2)} MB across ${tracks.length} tracks)`);

  // -------------------------------------------------------------------------
  // 2. Multi-Threaded Decryption in Shaka Decryptor
  // -------------------------------------------------------------------------
  console.log("\n--- [2. Concurrent Multi-Stream Decryption in C++ Engine] ---");
  console.log("  Registering all streams to a single Shaka Decryptor context...");
  console.log("  Shaka Packager will spawn dedicated C++ threads for each stream.");

  const workerPath = path.resolve(__dirname, "decrypt_worker.ts");
  const tWorker0 = performance.now();
  const worker = new Worker(new URL(workerPath, import.meta.url).href);

  const workerResponse: any = await new Promise((resolve, reject) => {
    const jobId = `job_multi_${Date.now()}`;

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
      streams: downloadResults.map((r) => ({
        name: r.track.name,
        inputPath: r.inputPath,
        outputPath: path.resolve(r.track.outputFile),
      })),
      verbose: false,
    });
  });

  const totalWorkerE2E = performance.now() - tWorker0;
  const globalElapsed = performance.now() - globalStart;

  // Clean temporary downloaded input files
  for (const r of downloadResults) {
    if (fs.existsSync(r.inputPath)) {
      fs.unlinkSync(r.inputPath);
    }
  }

  const { results, timings } = workerResponse;
  const pureDecryptTime = timings.decryptRunMs;
  const decryptThroughput = (totalDownloadedBytes / 1024 / 1024) / (pureDecryptTime / 1000);

  // -------------------------------------------------------------------------
  // 3. Print Results & Benchmark Table
  // -------------------------------------------------------------------------
  console.log("\n===================================================================");
  console.log("               MULTI-TRACK EXECUTION BENCHMARKS                    ");
  console.log("===================================================================");
  console.log(" Track Name     Type   Rep ID    Input Size    Decrypted Output File");
  console.log("-------------------------------------------------------------------");
  for (const r of downloadResults) {
    const resItem = results.find((res: any) => res.name === r.track.name);
    const outSize = resItem ? resItem.outputSize : 0;
    console.log(` ${r.track.name.padEnd(14)} ${r.track.type.padEnd(6)} ${r.track.repId.padStart(6)}   ${(r.bytes / 1024 / 1024).toFixed(2).padStart(6)} MB    ${path.resolve(r.track.outputFile)} (${outSize.toLocaleString()} B)`);
  }
  console.log("-------------------------------------------------------------------");
  console.log(" Parallel Download (Wall Time)             : " + `${totalDownloadWallTime.toFixed(2)} ms`.padStart(11));
  console.log(" C++ Concurrent Decryption (All Tracks)    : " + `${pureDecryptTime.toFixed(2)} ms`.padStart(11) + ` | ${decryptThroughput.toFixed(2)} MB/s`);
  console.log(" Total Worker Roundtrip (IPC + Run)        : " + `${totalWorkerE2E.toFixed(2)} ms`.padStart(11));
  console.log(" Total End-to-End Pipeline Time            : " + `${globalElapsed.toFixed(2)} ms (${(globalElapsed / 1000).toFixed(2)} s)`.padStart(11));
  console.log(" Total Processed Media Data                : " + `${(totalDownloadedBytes / 1024 / 1024).toFixed(2)} MB (${totalDownloadedBytes.toLocaleString()} bytes)`);
  console.log(" Jitter Buffer Setting                     : " + `${maxBuffered} segments max in RAM per track`);
  console.log(" Aggregated Decryption Throughput          : " + `${decryptThroughput.toFixed(2)} MB/s (${(decryptThroughput * 8).toFixed(1)} Mbps)`);
  console.log("===================================================================\n");
}

await runMultiStreamDecryption({ numSegments: 50, maxBufferedSegments: 10 });
