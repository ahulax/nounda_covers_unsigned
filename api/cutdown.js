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
  parseSegmentAnalysis, wordsInWindow, chunkWords, snapToSentence, probeDuration,
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
  const { segment } = clip;
  const fieldNames = SEGMENT_FIELDS[segment];
  if (!fieldNames) throw new Error(`clips[${index}]: unknown segment "${segment}"`);
  const videoUrl = fields[fieldNames.url];
  if (!videoUrl) throw new Error(`clips[${index}]: record has no ${fieldNames.url}`);

  // A caption-less Cutdown is a silent hard-requirement violation (spec: captions are
  // always burned in, never left to platform auto-captions), so this must fail loudly
  // rather than quietly render a substandard video. Root cause seen in production: a
  // Video Production record whose Analysis JSON field was simply empty (Gemini analysis
  // never ran on that segment) -- SF04's segment-picker only checked for a video URL, not
  // for analysis data, so it can pick a segment with video but no transcript.
  const analysis = parseSegmentAnalysis(fields[fieldNames.analysis]);
  if (!analysis) {
    throw new Error(
      `clips[${index}]: ${fieldNames.analysis} is missing or unparseable for segment "${segment}" -- cannot burn in captions. Refusing to render a subtitle-less clip.`
    );
  }
  const transcript = Array.isArray(analysis.transcript) ? analysis.transcript : [];

  // The model picks times off the analysis JSON and regularly lands mid-sentence, which
  // reads as an abrupt jump at a clip join and as a sentence the avatar never finishes at
  // the end. Both edges get pulled onto real sentence boundaries here, and the caption
  // window is derived from the same snapped bounds so speech and subtitles always agree.
  const { start, end } = snapToSentence(transcript, clip.start, clip.end, MAX_CLIP_SECONDS);

  const duration = Math.min(end - start, MAX_CLIP_SECONDS);
  if (!(duration > 0)) throw new Error(`clips[${index}]: end must be greater than start`);

  const shiftedWords = wordsInWindow(transcript, start, end);
  const chunks = chunkWords(shiftedWords);
  if (chunks.length === 0) {
    throw new Error(
      `clips[${index}]: no transcript words fall inside [${start}, ${end}] for segment "${segment}" -- cannot burn in captions. Refusing to render a subtitle-less clip.`
    );
  }

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

// Each clip is a separate avatar take, so a hard splice drops the viewer into the middle
// of a gesture with speech already running: the incoming segment's first word frequently
// starts at 0.00s, leaving no settle-in at all. A short dissolve on picture and audio
// reads as a deliberate transition instead of a jump. Kept brief so it never feels like a
// slideshow wipe.
const JOIN_FADE_SECONDS = 0.25;

async function joinSegments(segmentPaths, listPath, outputPath) {
  if (segmentPaths.length === 1) {
    await fs.writeFile(listPath, `file '${segmentPaths[0]}'`);
    await run(ffmpegPath, [
      "-y", "-f", "concat", "-safe", "0", "-i", listPath,
      "-c", "copy", "-movflags", "+faststart", outputPath,
    ]);
    return;
  }

  const durations = [];
  for (const p of segmentPaths) {
    const d = await probeDuration(p);
    if (!d) throw new Error(`could not probe duration of ${p} -- cannot place the join fades`);
    durations.push(d);
  }

  const args = ["-y"];
  for (const p of segmentPaths) args.push("-i", p);

  let filter = "";
  let vLabel = "0:v";
  let aLabel = "0:a";
  // Both streams shorten by the fade length at every join, so picture and audio stay in
  // step as the running offset accumulates.
  let offset = durations[0];
  for (let i = 1; i < segmentPaths.length; i++) {
    const v = `v${i}`;
    const a = `a${i}`;
    const at = (offset - JOIN_FADE_SECONDS).toFixed(3);
    filter += `[${vLabel}][${i}:v]xfade=transition=fade:duration=${JOIN_FADE_SECONDS}:offset=${at}[${v}];`;
    filter += `[${aLabel}][${i}:a]acrossfade=d=${JOIN_FADE_SECONDS}:c1=tri:c2=tri[${a}];`;
    vLabel = v;
    aLabel = a;
    offset = offset + durations[i] - JOIN_FADE_SECONDS;
  }

  args.push(
    "-filter_complex", filter.replace(/;$/, ""),
    "-map", `[${vLabel}]`, "-map", `[${aLabel}]`,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
    outputPath
  );
  await run(ffmpegPath, args);
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

    await joinSegments(segmentPaths, listPath, outputPath);

    const outputUrl = await uploadToCloudinary(outputPath);
    res.status(200).json({ output_url: outputUrl });
  } catch (err) {
    res.status(500).json({ error: String(err && err.message || err), stderr: err && err.stderr });
  } finally {
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
};
