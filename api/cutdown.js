/**
 * POST /api/cutdown — render one Format A (YouTube Cutdown) short-form video.
 *
 * A 9:16 excerpt built from 1-3 raw HeyGen avatar clips already sitting in Airtable's
 * Video Production table (Cold Open / Part N Intro / Closing Video URL fields) -- these
 * are UNCOMPOSITED source footage (no captions, no panels baked in, confirmed by reading
 * nounda-remotion directly), so this endpoint owns the entire vertical composite itself
 * rather than reusing the main pipeline's Remotion render. Two reasons that render isn't
 * reused: (1) its captions are sized for a 1920px-wide frame -- a plain crop to 1080px
 * would slice every caption line in half; (2) the real Remotion render runs async via a
 * GitHub Actions repository_dispatch (built for full 8-10 minute videos), architecturally
 * heavy for a 30-60s clip that needs a fast, direct render like Formats B and C already
 * have.
 *
 * Unlike B and C (silent, text-carried), Format A keeps its audio track -- per the
 * 2026-09-11 decision, spoken narration is Format-A only, since this is cut from footage
 * that already has natural speech. Captions are still burned in (word-timed, chunked into
 * short phrases from the real Gemini video-analysis transcript), same editorial-serif
 * design as B/C for cross-format brand consistency.
 *
 * Request body:
 *   {
 *     "record_id": "recLKh7RP1HO5yLrO",   Video Production record -- this endpoint fetches
 *                                          the segments' Video URL + Analysis JSON fields
 *                                          directly from Airtable rather than having Make
 *                                          pass the raw analysis text through: that field is
 *                                          a multi-KB JSON blob full of quotes/newlines/
 *                                          backslashes that would break Make's hand-built
 *                                          JSON string templates, unlike B/C's short caption
 *                                          text. Same reasoning /api/video-url already uses.
 *     "clips": [
 *       { "segment": "cold_open", "start": 0, "end": 10.36 },
 *       ... 1-3 clips, segment one of cold_open/part_1_intro/part_2_intro/part_3_intro/closing ...
 *     ]
 *   }
 * Response: { "output_url": "https://res.cloudinary.com/..." }
 */
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const ffmpegPath = require("ffmpeg-static");
const {
  run, uploadToCloudinary, fetchChunkCaptionPng, fetchAirtableRecord,
  parseSegmentAnalysis, wordsInWindow, chunkWords,
} = require("../lib/shortform_utils");

const MAX_CLIPS = 3;
const MAX_CLIP_SECONDS = 60;

const VIDEO_BASE_ID = "appraw1aDLqrHLY7q";
const VIDEO_TABLE_ID = "tbliFOuhLJF1x4CmV"; // Video Production
const SEGMENT_FIELDS = {
  cold_open: { url: "Cold Open Video URL", analysis: "Cold Open Analysis JSON" },
  part_1_intro: { url: "Part 1 Intro Video URL", analysis: "Part 1 Intro Analysis JSON" },
  part_2_intro: { url: "Part 2 Intro Video URL", analysis: "Part 2 Intro Analysis JSON" },
  part_3_intro: { url: "Part 3 Intro Video URL", analysis: "Part 3 Intro Analysis JSON" },
  closing: { url: "Closing Video URL", analysis: "Closing Analysis JSON" },
};

async function renderClip(workDir, index, clip, fields) {
  const { segment, start, end } = clip;
  const fieldNames = SEGMENT_FIELDS[segment];
  if (!fieldNames) throw new Error(`clips[${index}]: unknown segment "${segment}"`);
  const videoUrl = fields[fieldNames.url];
  if (!videoUrl) throw new Error(`clips[${index}]: record has no ${fieldNames.url}`);

  const duration = Math.min(end - start, MAX_CLIP_SECONDS);
  if (!(duration > 0)) throw new Error(`clips[${index}]: end must be greater than start`);

  const analysis = parseSegmentAnalysis(fields[fieldNames.analysis]);
  const transcript = analysis && Array.isArray(analysis.transcript) ? analysis.transcript : [];
  const shiftedWords = wordsInWindow(transcript, start, end);
  const chunks = chunkWords(shiftedWords);

  const chunkPngPaths = [];
  for (let i = 0; i < chunks.length; i++) {
    const png = await fetchChunkCaptionPng(chunks[i].text);
    const p = path.join(workDir, `clip${index}-chunk${i}.png`);
    await fs.writeFile(p, png);
    chunkPngPaths.push(p);
  }

  const segmentPath = path.join(workDir, `segment-${index}.mp4`);
  // -ss and -t must BOTH precede the -i they apply to. Placed after -i (even before the
  // next -i, for the caption overlay images), -t was empirically found to be silently
  // ignored -- the clip read to the end of the source file instead of trimming, verified
  // by a local test where a 25.86s intended trim came out as 59.78s (the untrimmed
  // remainder from the seek point) until this was fixed.
  const args = ["-y", "-ss", String(start), "-t", String(duration), "-i", videoUrl];
  for (const p of chunkPngPaths) args.push("-i", p);

  let filter =
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase," +
    "crop=1080:1920,setsar=1,fps=30[bg]";
  let prevLabel = "bg";
  chunks.forEach((chunk, i) => {
    const inputIdx = i + 1; // input 0 is the video; overlays start at 1
    const outLabel = i === chunks.length - 1 ? "vout" : `v${i}`;
    filter += `;[${prevLabel}][${inputIdx}:v]overlay=0:0:enable='between(t,${chunk.start},${chunk.end})'[${outLabel}]`;
    prevLabel = outLabel;
  });
  if (chunks.length === 0) filter = filter.replace("[bg]", "[vout]");

  args.push(
    "-filter_complex", filter, "-map", "[vout]", "-map", "0:a?",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k",
    segmentPath
  );

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
  const { record_id: recordId, clips } = body;

  if (!recordId) {
    res.status(400).json({ error: "record_id is required" });
    return;
  }
  if (!Array.isArray(clips) || clips.length < 1 || clips.length > MAX_CLIPS) {
    res.status(400).json({ error: `clips must be an array of 1-${MAX_CLIPS} {segment, start, end} items` });
    return;
  }
  for (const [i, c] of clips.entries()) {
    if (!c || !c.segment || typeof c.start !== "number" || typeof c.end !== "number") {
      res.status(400).json({ error: `clips[${i}] is missing segment, start, or end` });
      return;
    }
  }

  const workDir = await fs.mkdtemp(path.join(os.tmpdir(), "cutdown-"));
  const outputPath = path.join(workDir, "output.mp4");
  const listPath = path.join(workDir, "list.txt");

  try {
    const fields = await fetchAirtableRecord(VIDEO_BASE_ID, VIDEO_TABLE_ID, recordId);
    const segmentPaths = await Promise.all(clips.map((c, i) => renderClip(workDir, i, c, fields)));

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
