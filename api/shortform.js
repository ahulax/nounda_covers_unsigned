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
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const ffmpegPath = require("ffmpeg-static");
const {
  run, probeDuration, fetchCaptionPng, uploadToCloudinary, generateMusicBed, fetchMusicTrendStyle,
} = require("../lib/shortform_utils");

const MAX_DURATION = 30; // safety ceiling regardless of variant

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

    // Silent renders into its own file first (no audio); a generated music bed is muxed
    // in afterward, so a probe/generation failure can't take down the Voiced path too.
    const renderPath = variant === "Silent" ? path.join(workDir, "silent.mp4") : outputPath;

    args.push(
      "-t", String(targetDuration),
      "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
      "-movflags", "+faststart",
      renderPath
    );

    await run(ffmpegPath, args);

    if (variant === "Silent") {
      const moodNote = await fetchMusicTrendStyle();
      const musicPath = await generateMusicBed(targetDuration, workDir, moodNote);
      await run(ffmpegPath, [
        "-y", "-i", renderPath, "-i", musicPath,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart",
        outputPath,
      ]);
    }

    const outputUrl = await uploadToCloudinary(outputPath);
    res.status(200).json({ output_url: outputUrl });
  } catch (err) {
    res.status(500).json({ error: String(err && err.message || err), stderr: err && err.stderr });
  } finally {
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
};
