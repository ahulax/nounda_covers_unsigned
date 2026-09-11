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

module.exports = { run, probeDuration, fetchCaptionPng, uploadToCloudinary, selfBase };
