/**
 * File-Based Decryption Scenario in Bun (Worker Thread)
 * ====================================================
 * 
 * Demonstrates file-based decryption using a dedicated Worker thread,
 * with precise per-step timing benchmarks.
 * 
 * Run with:
 *   bun examples/bun/decrypt_scenario.ts
 */

import path from "path";
import fs from "fs";

export async function decryptFilesAsync(params: {
  kid: string;
  key: string;
  streams: { name: string; inputPath: string; outputPath: string }[];
  verbose?: boolean;
}): Promise<any> {
  const workerPath = path.resolve(__dirname, "decrypt_worker.ts");
  const t0 = performance.now();
  const worker = new Worker(new URL(workerPath, import.meta.url).href);

  return new Promise((resolve, reject) => {
    const jobId = `job_${Date.now()}`;

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
      ...params,
    });
  });
}

async function main() {
  const globalStart = performance.now();

  console.log("===================================================================");
  console.log("        FILE-BASED DECRYPTION & BENCHMARK (BUN WORKER)             ");
  console.log("===================================================================");

  const inputVideo = "bun_sample_encrypted.mp4";
  const outputVideo = "bun_scenario_decrypted.mp4";

  console.log("\n--- [1. Preparing Test Media File] ---");
  const tPrep0 = performance.now();
  const initResp = await fetch("https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/1/init.mp4");
  const seg1Resp = await fetch("https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/1/0001.m4s");
  const seg2Resp = await fetch("https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/1/0002.m4s");

  const combined = Buffer.concat([
    Buffer.from(await initResp.arrayBuffer()),
    Buffer.from(await seg1Resp.arrayBuffer()),
    Buffer.from(await seg2Resp.arrayBuffer()),
  ]);

  await Bun.write(inputVideo, combined);
  const prepMs = performance.now() - tPrep0;
  console.log(`[+] Created test input file: ${inputVideo} (${combined.length.toLocaleString()} bytes in ${prepMs.toFixed(1)} ms)`);

  const kid = "nrQFDeRLSAKTLifXUIPiZg";
  const key = "FmY0xnWCPCNaSpRG-tUuTQ";

  console.log("\n--- [2. Running Decryption on Background Worker Thread] ---");
  console.log("  Main JS event loop remains 100% active and non-blocking.");

  const response = await decryptFilesAsync({
    kid,
    key,
    streams: [
      {
        name: "video_track",
        inputPath: path.resolve(inputVideo),
        outputPath: path.resolve(outputVideo),
      },
    ],
    verbose: false,
  });

  const globalElapsed = performance.now() - globalStart;
  const { results, timings, workerRoundtripMs } = response;
  const totalBytes = combined.length;
  const decryptSpeed = (totalBytes / 1024 / 1024) / (timings.decryptRunMs / 1000);

  console.log("\n===================================================================");
  console.log("                  DETAILED STEP BENCHMARKS                         ");
  console.log("===================================================================");
  console.log(` Preparation & Media Download              : ${prepMs.toFixed(2).padStart(8)} ms | ${totalBytes.toLocaleString()} bytes`);
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
  console.log(` Total Data Processed                      : ${(totalBytes / 1024 / 1024).toFixed(2)} MB (${totalBytes.toLocaleString()} bytes)`);
  console.log(` Pure Decryption Throughput                : ${decryptSpeed.toFixed(2)} MB/s (${(decryptSpeed * 8).toFixed(1)} Mbps)`);
  console.log(` Decrypted Output File                     : ${path.resolve(outputVideo)}`);
  console.log("===================================================================\n");

  if (fs.existsSync(inputVideo)) fs.unlinkSync(inputVideo);
}

await main();
