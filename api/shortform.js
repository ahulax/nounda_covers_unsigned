/**
 * POST /api/shortform — render one Format C (Daily Hook) short-form video.
 *
 * Owns everything SF03 (the Make scenario) shouldn't have to: fetching the caption
 * overlay from api/caption.py (Python does the text layout — see that file for why),
 * compositing it onto the b-roll clip with ffmpeg, optionally mixing in the ElevenLabs
 * voiceover, and uploading the result to Cloudinary. Make just POSTs the record data
 * and gets back { output_url }.
 *
 * Request body:
 *   {
 *     "video_url": "https://videos.pexels.com/...mp4",   required, from B-Roll Bank
 *     "hook_text": "...",                                 required
 *     "supporting_line": "...",                            optional
 *     "audio_url": "https://.../narration.mp3",             required only when variant=Voiced
 *     "variant": "Silent" | "Voiced",                       default "Silent"
 *     "duration": 18                                        optional, seconds, default 18
 *   }
 * Response: { "output_url": "https://res.cloudinary.com/..." }
 *
 * Env vars (Vercel → Settings → Environment Variables):
 *   SELF_BASE_URL       e.g. https://nounda-cover.vercel.app (falls back to VERCEL_URL)
 *   CLOUDINARY_CLOUD    default "dvjshgv9h" (same account every other scenario uses)
 *   CLOUDINARY_PRESET_VIDEO   default "nounda_audio_unsigned" — Cloudinary treats audio
 *                             AND video as the same "video" resource type, so the preset
 *                             S12 already uses for narration uploads works here unchanged.
 */
const { execFile } = require("child_process");
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const ffmpegPath = require("ffmpeg-static");
const ffprobePath = require("ffprobe-static").path;

const MAX_DURATION = 30; // safety ceiling regardless of variant

const CLOUD = process.env.CLOUDINARY_CLOUD || "dvjshgv9h";
const PRESET = process.env.CLOUDINARY_PRESET_VIDEO || "nounda_audio_unsigned";

function selfBase() {
  if (process.env.SELF_BASE_URL) return process.env.SELF_BASE_URL.replace(/\/$/, "");
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return "";
}

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { maxBuffer: 1024 * 1024 * 64 }, (err, stdout, stderr) => {
      if (err) {
        err.stderr = stderr;
        return reject(err);
      }
      resolve({ stdout, stderr });
    });
  });
}

async function probeDuration(url) {
  const { stdout } = await run(ffprobePath, [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", url,
  ]);
  const d = parseFloat(stdout);
  return Number.isFinite(d) && d > 0 ? d : null;
}

async function fetchCaptionPng(hookText, supportingLine) {
  const base = selfBase();
  if (!base) throw new Error("SELF_BASE_URL / VERCEL_URL not set — cannot reach /api/caption");
  const res = await fetch(`${base}/api/caption`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hook_text: hookText, supporting_line: supportingLine }),
  });
  if (!res.ok) throw new Error(`caption render failed: ${res.status} ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
}

async function uploadToCloudinary(filePath) {
  const fileBuffer = await fs.readFile(filePath);
  const form = new FormData();
  form.append("file", new Blob([fileBuffer], { type: "video/mp4" }), "shortform.mp4");
  form.append("upload_preset", PRESET);
  const res = await fetch(`https://api.cloudinary.com/v1_1/${CLOUD}/video/upload`, {
    method: "POST",
    body: form,
  });
  const json = await res.json();
  if (!res.ok) throw new Error(`Cloudinary upload failed: ${JSON.stringify(json)}`);
  return json.secure_url;
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST only" });
    return;
  }

  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  const {
    video_url: videoUrl,
    hook_text: hookText,
    supporting_line: supportingLine = "",
    audio_url: audioUrl,
    variant = "Silent",
    duration = 18,
  } = body;

  if (!videoUrl || !hookText) {
    res.status(400).json({ error: "video_url and hook_text are required" });
    return;
  }
  if (variant === "Voiced" && !audioUrl) {
    res.status(400).json({ error: "audio_url is required when variant=Voiced" });
    return;
  }

  const workDir = await fs.mkdtemp(path.join(os.tmpdir(), "sf-"));
  const overlayPath = path.join(workDir, "overlay.png");
  const outputPath = path.join(workDir, "output.mp4");

  try {
    const png = await fetchCaptionPng(hookText, supportingLine);
    await fs.writeFile(overlayPath, png);

    // -shortest alone does not reliably stop encoding at the shorter stream's end
    // when the video is fed through -filter_complex (verified empirically: it kept
    // the full ~12s video length against an 8s audio track). Probing the audio's
    // real duration and passing it via -t is the fix that actually works.
    let targetDuration = Math.min(Number(duration) || 18, MAX_DURATION);
    if (variant === "Voiced") {
      const audioDuration = await probeDuration(audioUrl);
      if (audioDuration) targetDuration = Math.min(audioDuration, MAX_DURATION);
    }

    const filter =
      "[0:v]scale=1080:1920:force_original_aspect_ratio=increase," +
      "crop=1080:1920,setsar=1,fps=30[bg];[bg][1:v]overlay=0:0:format=auto[vout]";

    const args = ["-y", "-i", videoUrl, "-i", overlayPath];
    if (variant === "Voiced") args.push("-i", audioUrl);

    args.push("-filter_complex", filter, "-map", "[vout]");
    if (variant === "Voiced") {
      args.push("-map", "2:a", "-shortest", "-c:a", "aac", "-b:a", "128k");
    } else {
      args.push("-an");
    }

    args.push(
      "-t", String(targetDuration),
      "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
      "-movflags", "+faststart",
      outputPath
    );

    await run(ffmpegPath, args);
    const outputUrl = await uploadToCloudinary(outputPath);
    res.status(200).json({ output_url: outputUrl });
  } catch (err) {
    res.status(500).json({ error: String(err && err.message || err), stderr: err && err.stderr });
  } finally {
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
};
