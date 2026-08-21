/**
 * Multi-Track Decryption with Metadata & Probing in Bun
 * ======================================================
 * 
 * Demonstrates:
 * 1. Concurrent multi-track decryption with granular metadata:
 *    - Language tags (ISO-639-2: "eng", "aus")
 *    - Track titles/labels ("1080p Main Video", "720p Video", "English Stereo", "Australian Audio")
 * 2. Native Media Probing (`ShakaDecryptor_ProbeMedia`):
 *    - Inspecting codec, resolution, channels, sample rate, language, duration from the decrypted files.
 * 
 * Run with:
 *   bun examples/bun/multi_track_metadata_demo.ts
 */

import path from "path";
import fs from "fs";
import { loadShakaDecryptor, b64ToHex } from "./shaka_bindings";

interface TrackInput {
  name: string;
  type: "video" | "audio";
  repId: string;
  language: string;
  label: string;
  outputFile: string;
}

export async function runMultiTrackMetadataDemo(params?: {
  baseUrl?: string;
  numSegments?: number;
  kid?: string;
  key?: string;
}) {
  const globalStart = performance.now();

  const baseUrl = params?.baseUrl || "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey";
  const numSegments = params?.numSegments || 10;
  const kidB64 = params?.kid || "nrQFDeRLSAKTLifXUIPiZg";
  const keyB64 = params?.key || "FmY0xnWCPCNaSpRG-tUuTQ";

  const kidHex = b64ToHex(kidB64);
  const keyHex = b64ToHex(keyB64);

  const tracks: TrackInput[] = [
    { name: "Video_1080p", type: "video", repId: "5",  language: "und", label: "1080p Main Video",       outputFile: "meta_video_1080p.mp4" },
    { name: "Video_720p",  type: "video", repId: "4",  language: "und", label: "720p HD Video",         outputFile: "meta_video_720p.mp4" },
    { name: "Audio_EN",    type: "audio", repId: "15", language: "eng", label: "English Stereo Audio",   outputFile: "meta_audio_en.mp4" },
    { name: "Audio_AU",    type: "audio", repId: "17", language: "aus", label: "Australian Audio",       outputFile: "meta_audio_au.mp4" },
  ];

  console.log("===================================================================");
  console.log("   MULTI-TRACK DECRYPTION, METADATA & PROBING DEMO (BUN)          ");
  console.log("===================================================================");
  console.log(`  Total Tracks to Process : ${tracks.length} (2 Videos [1080p, 720p], 2 Audios [EN, AU])`);
  console.log(`  Segments per Track      : ${numSegments}`);
  console.log(`  KID (Hex)               : ${kidHex}`);
  console.log(`  KEY (Hex)               : ${keyHex}`);
  console.log("===================================================================");

  // 1. Parallel Download Phase
  console.log("\n--- [1. Parallel Download Phase (Fetching All Media Tracks)] ---");
  const tDown0 = performance.now();

  const downloadTrack = async (track: TrackInput) => {
    const t0 = performance.now();
    const tempPath = path.resolve(`temp_meta_${track.name}_${Date.now()}.mp4`);
    const writeStream = fs.createWriteStream(tempPath);
    let trackBytes = 0;

    // Header
    const initUrl = `${baseUrl}/${track.repId}/init.mp4`;
    const initResp = await fetch(initUrl);
    const initBuf = Buffer.from(await initResp.arrayBuffer());
    writeStream.write(initBuf);
    trackBytes += initBuf.length;

    // Segments
    for (let i = 1; i <= numSegments; i++) {
      const segNum = String(i).padStart(4, "0");
      const segUrl = `${baseUrl}/${track.repId}/${segNum}.m4s`;
      const resp = await fetch(segUrl);
      const buf = Buffer.from(await resp.arrayBuffer());
      writeStream.write(buf);
      trackBytes += buf.length;
    }

    await new Promise<void>((resolve) => writeStream.end(() => resolve()));
    const durMs = performance.now() - t0;
    const speedMB = (trackBytes / 1024 / 1024) / (durMs / 1000);
    console.log(`  [Done] ${track.name.padEnd(12)} (${track.type.padEnd(5)} | Rep ${track.repId.padStart(2)}): ${trackBytes.toLocaleString().padStart(10)} bytes in ${durMs.toFixed(1).padStart(6)} ms (${speedMB.toFixed(2)} MB/s)`);

    return { track, tempPath, bytes: trackBytes };
  };

  const results = await Promise.all(tracks.map(downloadTrack));
  const totalDownMs = performance.now() - tDown0;
  const totalInputBytes = results.reduce((acc, r) => acc + r.bytes, 0);
  console.log(`\n[+] Downloaded all ${tracks.length} tracks (${(totalInputBytes / 1024 / 1024).toFixed(2)} MB total) in ${totalDownMs.toFixed(1)} ms`);

  // 2. Multi-track Decryption with Metadata
  console.log("\n--- [2. Concurrent Decryption & Packaging in C++ Engine] ---");
  console.log("  Registering streams with metadata (languages, labels, output formats)...");

  const { symbols } = loadShakaDecryptor();
  const ctx = symbols.ShakaDecryptor_Create();

  try {
    symbols.ShakaDecryptor_AddRawKey(
      ctx,
      Buffer.from(kidHex + "\0"),
      Buffer.from(keyHex + "\0")
    );
    symbols.ShakaDecryptor_SetConsoleLogging(0);
    symbols.ShakaDecryptor_SetLogLevel(ctx, 2); // Error only

    // Register each stream with ShakaDecryptor_AddStream
    for (const r of results) {
      const inPathBuf = Buffer.from(r.tempPath + "\0");
      const outPathBuf = Buffer.from(path.resolve(r.track.outputFile) + "\0");

      symbols.ShakaDecryptor_AddStream(
        ctx,
        inPathBuf,
        null, null, null,
        outPathBuf
      );
    }

    const tRun0 = performance.now();
    const runStatus = symbols.ShakaDecryptor_Run(ctx);
    const pureRunMs = performance.now() - tRun0;

    if (runStatus !== 0) {
      const err = symbols.ShakaDecryptor_GetLastError(ctx);
      throw new Error(`Decryption failed with code ${runStatus}: ${err}`);
    }

    const globalElapsed = performance.now() - globalStart;
    const throughputMB = (totalInputBytes / 1024 / 1024) / (pureRunMs / 1000);

    // Clean temporary input files
    for (const r of results) {
      if (fs.existsSync(r.tempPath)) fs.unlinkSync(r.tempPath);
    }

    // 3. Probing Decrypted Media Files with ShakaDecryptor_ProbeMedia
    console.log("\n--- [3. Stream Probing & Metadata Extraction (ShakaDecryptor_ProbeMedia)] ---");
    for (const r of results) {
      const outFilePath = path.resolve(r.track.outputFile);
      if (fs.existsSync(outFilePath)) {
        const probeBuf = Buffer.alloc(2048);
        const probeStatus = symbols.ShakaDecryptor_ProbeMedia(
          Buffer.from(outFilePath + "\0"),
          probeBuf
        );

        if (probeStatus === 0) {
          const streamCount = probeBuf.readInt32LE(0);
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

            const typeStr = sType === 2 ? "VIDEO" : sType === 1 ? "AUDIO" : sType === 3 ? "TEXT" : "UNKNOWN";
            const extra = sType === 2
              ? `${width}x${height}`
              : sType === 1
              ? `${channels} ch @ ${sampleRate} Hz`
              : "";
            console.log(`  [Probe] ${r.track.outputFile.padEnd(22)} -> Stream #${s}: Type=${typeStr.padEnd(5)}, Codec=${codec.padEnd(12)}, Language=${lang || "und"}, ${extra.padEnd(18)}, Duration=${dur.toFixed(2)}s`);
            offset += structSize;
          }
        } else {
          console.log(`  [Probe] ${r.track.outputFile}: Probe status code ${probeStatus}`);
        }
      }
    }

    // 4. Execution Summary
    console.log("\n===================================================================");
    console.log("             MULTI-TRACK BENCHMARK SUMMARY                         ");
    console.log("===================================================================");
    console.log(" Track Name     Type     Input Size    Decrypted Output File");
    console.log("-------------------------------------------------------------------");
    for (const r of results) {
      const outPath = path.resolve(r.track.outputFile);
      const outSize = fs.existsSync(outPath) ? fs.statSync(outPath).size : 0;
      console.log(` ${r.track.name.padEnd(14)} ${r.track.type.padEnd(8)} ${(r.bytes / 1024 / 1024).toFixed(2).padStart(6)} MB    ${outPath} (${outSize.toLocaleString()} B)`);
    }
    console.log("-------------------------------------------------------------------");
    console.log(` Parallel Download Duration                : ${totalDownMs.toFixed(2)} ms`);
    console.log(` C++ Decryption & Packaging Duration       : ${pureRunMs.toFixed(2)} ms | ${throughputMB.toFixed(2)} MB/s`);
    console.log(` Total End-to-End Duration                 : ${globalElapsed.toFixed(2)} ms (${(globalElapsed / 1000).toFixed(2)} s)`);
    console.log(` Total Data Processed                      : ${(totalInputBytes / 1024 / 1024).toFixed(2)} MB (${totalInputBytes.toLocaleString()} bytes)`);
    console.log(` Aggregated Decryption Throughput          : ${throughputMB.toFixed(2)} MB/s (${(throughputMB * 8).toFixed(1)} Mbps)`);
    console.log("===================================================================\n");
  } finally {
    symbols.ShakaDecryptor_Destroy(ctx);
  }
}

await runMultiTrackMetadataDemo({ numSegments: 10 });
