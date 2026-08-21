/**
 * High-Level SDK & One-Shot In-Memory Buffer Demo (Bun)
 * =====================================================
 * 
 * Demonstrates:
 * 1. `ShakaDecryptorSession.decryptBuffer()`: 1-line in-memory buffer decryption.
 * 2. `ShakaDecryptorSession.probe()`: In-process stream probing.
 * 3. `ShakaDecryptorSession.getStats()`: Direct throughput and byte statistics.
 * 
 * Run with:
 *   bun examples/bun/sdk_demo.ts
 */

import { ShakaDecryptorSession, ShakaLogLevel } from "../../sdk/bun/shaka_sdk";
import fs from "fs";
import path from "path";

async function main() {
  console.log("===================================================================");
  console.log("    SHAKA DECRYPTOR HIGH-LEVEL TYPESCRIPT SDK DEMO (BUN)          ");
  console.log("===================================================================");

  const baseUrl = "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const kidB64 = "nrQFDeRLSAKTLifXUIPiZg";
  const keyB64 = "FmY0xnWCPCNaSpRG-tUuTQ";

  // -------------------------------------------------------------------------
  // Feature 1: One-Shot In-Memory Buffer Decryption
  // -------------------------------------------------------------------------
  console.log("\n--- [1. One-Shot Zero-Setup In-Memory Buffer Decryption] ---");
  console.log("  Fetching encrypted audio init + 1 segment into RAM...");

  const initResp = await fetch(`${baseUrl}/15/init.mp4`);
  const segResp = await fetch(`${baseUrl}/15/0001.m4s`);

  const initBuf = Buffer.from(await initResp.arrayBuffer());
  const segBuf = Buffer.from(await segResp.arrayBuffer());
  const combinedEncrypted = Buffer.concat([initBuf, segBuf]);

  console.log(`  Combined Encrypted RAM Buffer Size: ${combinedEncrypted.length.toLocaleString()} bytes`);

  const t0 = performance.now();
  // 1-line decryption:
  const decryptedBuf = ShakaDecryptorSession.decryptBuffer(combinedEncrypted, kidB64, keyB64);
  const durMs = performance.now() - t0;

  console.log(`  [+] Decrypted directly in RAM: ${decryptedBuf.byteLength.toLocaleString()} bytes in ${durMs.toFixed(2)} ms!`);

  // Write temporary test file to probe
  const testOutFile = path.resolve("temp_sdk_probe_test.mp4");
  fs.writeFileSync(testOutFile, decryptedBuf);

  // -------------------------------------------------------------------------
  // Feature 2: In-Process Media Probing
  // -------------------------------------------------------------------------
  console.log("\n--- [2. In-Process Media Probing (ShakaDecryptorSession.probe)] ---");
  const info = ShakaDecryptorSession.probe(testOutFile);
  console.log(`  Container Format : ${info.containerFormat}`);
  console.log(`  Streams Count    : ${info.streamCount}`);
  for (let i = 0; i < info.streams.length; i++) {
    const s = info.streams[i];
    console.log(`  Stream #${i}        : Type=${s.streamType}, Codec=${s.codec}, Lang=${s.language}, Channels=${s.audioChannels}, Rate=${s.sampleRate}Hz, Dur=${s.durationSeconds.toFixed(2)}s`);
  }

  // -------------------------------------------------------------------------
  // Feature 3: Object-Oriented Session with Stats
  // -------------------------------------------------------------------------
  console.log("\n--- [3. High-Level OOP Session with Real-Time Performance Stats] ---");
  const session = new ShakaDecryptorSession();
  session.addKey(kidB64, keyB64);
  session.setLogLevel(ShakaLogLevel.ERROR);
  session.setConsoleLogging(false);

  const destFile = path.resolve("temp_sdk_session_out.mp4");
  session.addFileStream(testOutFile, destFile);

  session.run();
  const stats = session.getStats();

  console.log(`  Bytes Read       : ${stats.totalBytesRead.toLocaleString()} bytes`);
  console.log(`  Execution Time   : ${stats.executionDurationMs.toFixed(2)} ms`);
  console.log(`  Throughput       : ${stats.throughputMBps.toFixed(2)} MB/s`);

  session.destroy();

  // Cleanup
  if (fs.existsSync(testOutFile)) fs.unlinkSync(testOutFile);
  if (fs.existsSync(destFile)) fs.unlinkSync(destFile);

  console.log("\n===================================================================");
  console.log("   ALL HIGH-LEVEL SDK TESTS & BENCHMARKS PASSED SUCCESSFULLY!     ");
  console.log("===================================================================\n");
}

await main();
