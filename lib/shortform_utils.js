/**
 * Shared helpers for the short-form render endpoints (api/shortform.js, api/mechanism.js).
 * Kept in one place because both need the identical ffmpeg-run wrapper, the same
 * /api/caption fetch, and the same Cloudinary upload -- duplicating them risks the two
 * endpoints silently drifting apart on a fix (e.g. the -shortest workaround documented
 * below) the way api/caption.py's routing bug did before it was centralized.
 */
const { execFile } = require("child_process");
const fs = require("fs/promises");
const ffprobePath = require("ffprobe-static").path;

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
  if (!base) throw new Error("SELF_BASE_URL / VERCEL_URL not set -- cannot reach /api/caption");
  const res = await fetch(`${base}/api/caption`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hook_text: hookText, supporting_line: supportingLine || "" }),
  });
  if (!res.ok) throw new Error(`caption render failed: ${res.status} ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
}

async function fetchChunkCaptionPng(text) {
  const base = selfBase();
  if (!base) throw new Error("SELF_BASE_URL / VERCEL_URL not set -- cannot reach /api/caption-chunk");
  const res = await fetch(`${base}/api/caption-chunk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`chunk caption render failed: ${res.status} ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
}

/**
 * Gemini's raw video-analysis JSON (stored verbatim in Video Production's per-segment
 * "* Analysis JSON" fields) is malformed for any segment over ~60s: timestamps past the
 * one-minute mark come out as "1:00.2" (minute:second) instead of a decimal number,
 * e.g. `"start": 1:02.16` -- not valid JSON. Nothing in the main render pipeline parses
 * these fields today (confirmed by inspecting nounda-remotion directly), so this bug has
 * never surfaced before. Found while building Format A, the first real consumer.
 */
function sanitizeGeminiTimestamps(rawText) {
  return rawText.replace(
    /("start"|"end"|"time")(\s*:\s*)(\d+):(\d+(?:\.\d+)?)/g,
    (_, key, sep, mins, secs) => `${key}${sep}${Number(mins) * 60 + Number(secs)}`
  );
}

/** Extract the {transcript, highlight_quotes, beat_markers, summary, ...} object Gemini
 * produced for one avatar segment, out of the outer Gemini API response wrapper Airtable
 * stores verbatim. Returns null if the field is empty or genuinely unparseable. */
function parseSegmentAnalysis(rawField) {
  if (!rawField) return null;
  try {
    const outer = JSON.parse(rawField);
    const innerText = outer.candidates[0].content.parts[0].text;
    return JSON.parse(sanitizeGeminiTimestamps(innerText));
  } catch (err) {
    return null;
  }
}

/** Words whose [start,end] falls inside [clipStart,clipEnd] (small tolerance for
 * boundary words), time-shifted so 0 is the start of the trimmed clip. */
function wordsInWindow(words, clipStart, clipEnd, tolerance = 0.15) {
  return (words || [])
    .filter((w) => w.start >= clipStart - tolerance && w.end <= clipEnd + tolerance)
    .map((w) => ({
      word: w.word,
      start: Math.max(0, w.start - clipStart),
      end: Math.max(0, w.end - clipStart),
    }));
}

/** Group word timings into short on-screen caption chunks: a new chunk starts after
 * maxWords or when the gap since the previous word exceeds maxGapSeconds (a natural
 * speech pause), whichever comes first. */
function chunkWords(words, maxWords = 4, maxGapSeconds = 0.35) {
  const chunks = [];
  let current = [];
  for (const w of words) {
    if (current.length > 0) {
      const gap = w.start - current[current.length - 1].end;
      if (gap > maxGapSeconds || current.length >= maxWords) {
        chunks.push(current);
        current = [];
      }
    }
    current.push(w);
  }
  if (current.length) chunks.push(current);
  return chunks.map((c) => ({
    text: c.map((w) => w.word).join(" "),
    start: c[0].start,
    end: c[c.length - 1].end,
  }));
}

async function uploadToCloudinary(filePath, resourceType = "video") {
  const fileBuffer = await fs.readFile(filePath);
  const form = new FormData();
  form.append("file", new Blob([fileBuffer], { type: "video/mp4" }), "output.mp4");
  form.append("upload_preset", PRESET);
  const res = await fetch(`https://api.cloudinary.com/v1_1/${CLOUD}/${resourceType}/upload`, {
    method: "POST",
    body: form,
  });
  const json = await res.json();
  if (!res.ok) throw new Error(`Cloudinary upload failed: ${JSON.stringify(json)}`);
  return json.secure_url;
}

module.exports = {
  run, probeDuration, fetchCaptionPng, fetchChunkCaptionPng, uploadToCloudinary, selfBase,
  parseSegmentAnalysis, wordsInWindow, chunkWords,
};
