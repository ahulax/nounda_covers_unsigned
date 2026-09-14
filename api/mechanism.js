/**
 * POST /api/mechanism — render one Format B (Mechanism Explainer) short-form video.
 *
 * Four beats (assumption / reality / stakes / action), each its own b-roll clip with
 * its own caption, cut together into one video. No spoken narration (per the 2026-09-11
 * decision: spoken narration is Format-A only) -- but DOES carry a generated instrumental
 * music bed as of 2026-09-12, after Daniil confirmed silent-with-no-music read as dead air.
 * Music stays organic/acoustic only (never synth/corporate) but rotates across several
 * acoustic instrument families per render (see MUSIC_STYLE_VARIANTS in shortform_utils.js)
 * so consecutive reels don't all sound like solo piano.
 *
 * Request body:
 *   {
 *     "beats": [
 *       { "video_url": "https://videos.pexels.com/...mp4", "text": "..." },
 *       ... exactly 4 ...
 *     ],
 *     "beat_duration": 6   optional, seconds per beat, default 6 (4 beats ≈ 24s total)
 *   }
 * Response: { "output_url": "https://res.cloudinary.com/..." }
 *
 * Reuses the exact same caption design (api/caption.py, "editorial serif") and the
 * same scale/crop/overlay filter as api/shortform.js — this only adds trimming each
 * beat to a fixed duration and concatenating the four encoded segments afterward.
 */
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const ffmpegPath = require("ffmpeg-static");
const {
  run, fetchCaptionPng, uploadToCloudinary, generateMusicBed, fetchMusicTrendStyle,
} = require("../lib/shortform_utils");

const DEFAULT_BEAT_DURATION = 6;
const MAX_BEAT_DURATION = 10;

async function renderSegment(workDir, index, videoUrl, text, beatDuration) {
  const overlayPath = path.join(workDir, `overlay-${index}.png`);
  const segmentPath = path.join(workDir, `segment-${index}.mp4`);

  const png = await fetchCaptionPng(text, "");
  await fs.writeFile(overlayPath, png);

  const filter =
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase," +
    "crop=1080:1920,setsar=1,fps=30[bg];[bg][1:v]overlay=0:0:format=auto[vout]";

  const args = [
    "-y", "-i", videoUrl, "-i", overlayPath,
    "-filter_complex", filter, "-map", "[vout]", "-an",
    "-t", String(beatDuration),
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
    segmentPath,
  ];

  // This endpoint fetches 4 remote clips instead of shortform.js's 1, so it has 4x the
  // exposure to a transient network blip on the source URL (observed once, empirically,
  // while building this) -- one retry absorbs that without failing the whole render.
  try {
    await run(ffmpegPath, args);
  } catch (err) {
    await run(ffmpegPath, args);
  }
  return segmentPath;
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST only" });
    return;
  }

  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  const { beats, beat_duration: rawBeatDuration } = body;

  if (!Array.isArray(beats) || beats.length !== 4) {
    res.status(400).json({ error: "beats must be an array of exactly 4 {video_url, text} items" });
    return;
  }
  for (const [i, b] of beats.entries()) {
    if (!b || !b.video_url || !b.text) {
      res.status(400).json({ error: `beats[${i}] is missing video_url or text` });
      return;
    }
  }
  const beatDuration = Math.min(Number(rawBeatDuration) || DEFAULT_BEAT_DURATION, MAX_BEAT_DURATION);

  const workDir = await fs.mkdtemp(path.join(os.tmpdir(), "mech-"));
  const outputPath = path.join(workDir, "output.mp4");
  const listPath = path.join(workDir, "list.txt");

  try {
    // Rendered concurrently, not sequentially: a live timing test hit 60.03s end to end,
    // right at Vercel Hobby's hard 60s cap (confirmed by the same call dropping outright
    // on a shorter client timeout). Each segment is a network fetch of a remote clip plus
    // a fast local encode -- running all four at once overlaps that fetch latency instead
    // of paying it four times. Promise.all preserves input order regardless of completion
    // order, so the concat list below still comes out in beat order.
    const segmentPaths = await Promise.all(
      beats.map((b, i) => renderSegment(workDir, i, b.video_url, b.text, beatDuration))
    );

    const listContent = segmentPaths.map((p) => `file '${p}'`).join("\n");
    await fs.writeFile(listPath, listContent);

    const silentPath = path.join(workDir, "silent.mp4");
    await run(ffmpegPath, [
      "-y", "-f", "concat", "-safe", "0", "-i", listPath,
      "-c", "copy", "-movflags", "+faststart",
      silentPath,
    ]);

    const totalDuration = beatDuration * beats.length;
    const moodNote = await fetchMusicTrendStyle();
    const musicPath = await generateMusicBed(totalDuration, workDir, moodNote);

    await run(ffmpegPath, [
      "-y", "-i", silentPath, "-i", musicPath,
      "-map", "0:v", "-map", "1:a",
      "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
      "-shortest", "-movflags", "+faststart",
      outputPath,
    ]);

    const outputUrl = await uploadToCloudinary(outputPath);
    res.status(200).json({ output_url: outputUrl });
  } catch (err) {
    res.status(500).json({ error: String(err && err.message || err), stderr: err && err.stderr });
  } finally {
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
};
