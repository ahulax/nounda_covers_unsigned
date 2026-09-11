/**
 * POST /api/mechanism — render one Format B (Mechanism Explainer) short-form video.
 *
 * Four beats (assumption / reality / stakes / action), each its own b-roll clip with
 * its own caption, cut together into one video. Silent — no voiceover, no music yet
 * (per the 2026-09-11 decision: spoken narration is Format-A only; sound design for
 * B/C is a separate, still-unsolved problem, not blocked on by this endpoint).
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
const { run, fetchCaptionPng, uploadToCloudinary } = require("../lib/shortform_utils");

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
    const segmentPaths = [];
    for (let i = 0; i < beats.length; i++) {
      const seg = await renderSegment(workDir, i, beats[i].video_url, beats[i].text, beatDuration);
      segmentPaths.push(seg);
    }

    const listContent = segmentPaths.map((p) => `file '${p}'`).join("\n");
    await fs.writeFile(listPath, listContent);

    await run(ffmpegPath, [
      "-y", "-f", "concat", "-safe", "0", "-i", listPath,
      "-c", "copy", "-movflags", "+faststart",
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
