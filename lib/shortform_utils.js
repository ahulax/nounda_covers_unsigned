/**
 * Shared helpers for the short-form render endpoints (api/shortform.js, api/mechanism.js).
 * Kept in one place because both need the identical ffmpeg-run wrapper, the same
 * /api/caption fetch, and the same Cloudinary upload -- duplicating them risks the two
 * endpoints silently drifting apart on a fix (e.g. the -shortest workaround documented
 * below) the way api/caption.py's routing bug did before it was centralized.
 */
const { execFile } = require("child_process");
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const ffprobePath = require("ffprobe-static").path;
const ffmpegPath = require("ffmpeg-static");

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

/** Fetch one Airtable record's fields directly by ID. Used instead of passing large field
 * values (e.g. a multi-KB raw analysis JSON blob, full of quotes/newlines/backslashes)
 * through Make's hand-built JSON string templates, which would break on that content --
 * the same reasoning /api/video-url already applies for S3 URL resolution. */
async function fetchAirtableRecord(baseId, tableId, recordId) {
  const pat = process.env.AIRTABLE_PAT;
  if (!pat) throw new Error("AIRTABLE_PAT is not set");
  const res = await fetch(`https://api.airtable.com/v0/${baseId}/${tableId}/${recordId}`, {
    headers: { Authorization: `Bearer ${pat}` },
  });
  if (!res.ok) throw new Error(`Airtable fetch failed: ${res.status} ${await res.text()}`);
  const data = await res.json();
  return data.fields || {};
}

// Locked 2026-09-12 after Daniil reviewed real rendered sketches: organic/acoustic only
// (solo piano, felt piano, strings, acoustic guitar) -- explicitly rejected anything
// synth/electronic-sounding as "corporate 2015" stock-video music. Never relax this floor
// even when a trend-style note (see fetchMusicTrendStyle below) suggests otherwise --
// trend signal may steer tempo/mood, never the instrument family.
const BASE_MUSIC_STYLE =
  "warm, organic, acoustic instrumental only -- solo piano or soft felt piano, intimate " +
  "and slightly melancholic, unhurried, warm room reverb, no strings section unless asked, " +
  "no synths, no electronic elements, no drums, restrained and human, never corporate or " +
  "tech-demo sounding";

// ElevenLabs Music reliably composes toward a quiet/decaying musical ending somewhere in
// the last ~10-25% of the requested duration (confirmed empirically across every style
// tested: solo piano, felt piano, guitar, strings all showed 0.5-5s of near-silence before
// the requested end). Requesting notably more than needed and using only the clean front
// portion avoids ever touching that decay zone.
const MUSIC_OVERSHOOT_MS = 16000;
const MUSIC_LOOP_CROSSFADE_S = 1.2;
const ELEVEN_MUSIC_URL = "https://api.elevenlabs.io/v1/music";

async function generateRawMusic(prompt, lengthMs) {
  const key = process.env.ELEVENLABS_API_KEY;
  if (!key) throw new Error("ELEVENLABS_API_KEY is not set");
  const res = await fetch(ELEVEN_MUSIC_URL, {
    method: "POST",
    headers: { "xi-api-key": key, "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, music_length_ms: lengthMs }),
  });
  if (!res.ok) throw new Error(`ElevenLabs Music generation failed: ${res.status} ${await res.text()}`);
  return Buffer.from(await res.arrayBuffer());
}

/**
 * Generate a background music bed of exactly `durationSeconds`, with no trailing dead air
 * and a short crossfade baked into its own tail so it blends smoothly when the platform
 * (Instagram/TikTok) loops the whole Reel back to frame 0. Returns a local mp3 file path.
 *
 * moodNote (optional): a short trend-style descriptor to blend into the prompt (tempo/mood
 * words only) -- see fetchMusicTrendStyle. The acoustic-only floor above is never relaxed.
 */
async function generateMusicBed(durationSeconds, workDir, moodNote) {
  const prompt = moodNote ? `${BASE_MUSIC_STYLE}. ${moodNote}` : BASE_MUSIC_STYLE;
  const overshootMs = Math.round(durationSeconds * 1000) + MUSIC_OVERSHOOT_MS;

  const rawPath = path.join(workDir, "music_raw.mp3");
  const rawBuf = await generateRawMusic(prompt, overshootMs);
  await fs.writeFile(rawPath, rawBuf);

  const mainEnd = durationSeconds - MUSIC_LOOP_CROSSFADE_S;
  const loopedPath = path.join(workDir, "music_bed.mp3");
  await run(ffmpegPath, [
    "-y", "-i", rawPath,
    "-filter_complex",
    `[0:a]atrim=0:${mainEnd},asetpts=PTS-STARTPTS[main];` +
      `[0:a]atrim=${mainEnd}:${durationSeconds},asetpts=PTS-STARTPTS[tail];` +
      `[0:a]atrim=0:${MUSIC_LOOP_CROSSFADE_S},asetpts=PTS-STARTPTS[head];` +
      `[tail][head]acrossfade=d=${MUSIC_LOOP_CROSSFADE_S}:c1=tri:c2=tri[blend];` +
      `[main][blend]concat=n=2:v=0:a=1[out]`,
    "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2",
    loopedPath,
  ]);
  return loopedPath;
}

/** Reads the current trend-style note (tempo/mood only, e.g. "lean slightly more upbeat
 * and rhythmic this week") from Airtable's shared Config table, written periodically by
 * the trend-scanning scenario. Returns null (not an error) if absent -- music generation
 * must work fine with just the locked base style when no trend note exists yet. */
async function fetchMusicTrendStyle() {
  const pat = process.env.AIRTABLE_PAT;
  if (!pat) return null;
  try {
    // The trend-scanning scenario always CREATEs a new Config row rather than updating one
    // in place (simpler than building upsert/Router logic in Make for a once-a-week job) --
    // so several rows can accumulate over time. Fetch a few and pick the newest by
    // createdTime client-side, since Airtable's list `sort` param only works on real fields.
    const res = await fetch(
      "https://api.airtable.com/v0/appraw1aDLqrHLY7q/tblaouuChYk3PNwvZ?" +
        "filterByFormula=" + encodeURIComponent("{Key}='music_trend_style'") + "&maxRecords=5",
      { headers: { Authorization: `Bearer ${pat}` } }
    );
    if (!res.ok) return null;
    const data = await res.json();
    const records = data.records || [];
    if (!records.length) return null;
    records.sort((a, b) => new Date(b.createdTime) - new Date(a.createdTime));
    return records[0].fields?.Value || null;
  } catch (err) {
    return null;
  }
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
  parseSegmentAnalysis, wordsInWindow, chunkWords, fetchAirtableRecord,
  generateMusicBed, fetchMusicTrendStyle,
};
