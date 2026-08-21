# nounda-cover

The YouTube cover renderer. One endpoint, called once per video by Make (S31).

It generates the identity-locked avatar portrait, composites the 1920×1080 cover, and
returns both URLs. All the slow, fiddly work (async image generation + polling) happens
here rather than in Make.

Design rules it implements: `YOUTUBE-PACKAGING-GUIDE.md` §1.

---

## Endpoint

### `POST /api/cover`

```json
{
  "record_id": "recd7myDOrfgizoPW",
  "cover_concept": {
    "number": "2",
    "word": "letters",
    "supporting": "decide whether DRIEETS shelves your dossier.",
    "headline": "",
    "kicker": "The Letters",
    "expression": "serious · concerned",
    "setting": "préfecture / administrative hall"
  },
  "title": "Why French Tech Visa Files Get Silently Shelved"
}
```

`cover_concept` accepts the object **or** the JSON string Airtable stores.

**Response**
```json
{
  "portrait_url": "https://…generated-portrait.jpg",
  "thumbnail_url": "https://res.cloudinary.com/…/nounda-covers/recd7…-1750000000.jpg"
}
```

Add `"dry_run": true` to skip generation and composite straight onto the bundled
reference still. Free, instant, and useful for checking layout changes.

### `GET /api/cover`
Health check. Returns `{ ok: true, reference: "<reference url>" }`.

---

## What it does

1. Builds a NanoBanana prompt from `expression` + `setting`, wrapped in the guide's
   guardrails (warm editorial palette, natural light, subject right / space left,
   no flags, no money, no passports, no glossy stock).
2. `POST https://api.piapi.ai/api/v1/task` with `model: gemini`,
   `task_type: nano-banana-2`, and the **locked avatar reference** in `image_urls`.
   That reference is what keeps the face identical across every video.
3. Polls server-side (3s × 15 ≈ 45s ceiling, inside Vercel's 60s function limit).
4. Composites the cover: warm grade + film grain, left-only dark scrim, inset frame,
   gold kicker, big numeral + gold bar, supporting line, logo with guaranteed clearance.
5. Uploads to Cloudinary as JPEG q90 (~220 KB, well under YouTube's 2 MB cap).

Cost: **$0.06 per portrait** at 1K, $0.08 at 2K (this uses 2K).

---

## Environment variables

Set in **Vercel → Project → Settings → Environment Variables**:

| Var | Value |
|---|---|
| `PIAPI_KEY` | Your PiAPI API key |
| `CLOUDINARY_CLOUD` | `dvjshgv9h` |
| `CLOUDINARY_PRESET` | An **unsigned** preset that accepts images (see below) |
| `SELF_BASE_URL` | `https://<your-deployment>.vercel.app` — used to serve the bundled reference still |
| `AVATAR_REFERENCE_URL` | *(optional)* override the reference image with any public URL |

### Creating the Cloudinary preset
Cloudinary → **Settings → Upload → Upload presets → Add upload preset**
- Signing mode: **Unsigned**
- Name it e.g. `nounda_covers_unsigned`
- Folder: `nounda-covers`

The existing `nounda_audio_unsigned` preset is scoped to audio; images need their own.

---

## The avatar reference

`assets/avatar-reference.jpg` is the **identity anchor**. Every generated portrait is
conditioned on it, which is what keeps the face recognisably the same person while the
scene, outfit, and expression change per video.

Change it rarely and deliberately — swapping it re-bases the channel's face.

---

## Local test

```bash
pip install Pillow
cd api && python3 -c "
import sys, json; sys.path.insert(0,'.')
from PIL import Image
from _render import render_cover
concept = {'number':'2','word':'letters','supporting':'decide whether DRIEETS shelves your dossier.','headline':'','kicker':'The Letters','expression':'serious · concerned','setting':'library'}
render_cover(Image.open('../assets/avatar-reference.jpg'), concept).save('/tmp/cover.jpg', quality=90)
print('wrote /tmp/cover.jpg')
"
```

---

## Notes

- **Timeout risk:** generation usually finishes in 15–40s. If PiAPI is slow the function
  can hit Vercel's 60s ceiling and return a `TimeoutError`. Re-running S31 is safe and
  cheap; if it becomes frequent, split into create-task and fetch-result calls.
- **Regenerating a cover:** clear `YouTube Thumbnail URL` on the record and re-run S31.
  Each attempt costs $0.08.
