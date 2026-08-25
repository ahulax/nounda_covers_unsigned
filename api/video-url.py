"""GET /api/video-url?record_id=<Video Production record id>

Fixes the root cause of the "S3 URL expired" bug: presigned S3 URLs are
hard-capped at 7 days by AWS (no way around it), but a video can sit in
`Ready to publish` for longer than that before S14 actually runs. Storing
the presigned URL in Airtable and trusting it later is inherently fragile.

This endpoint mints a FRESH presigned URL at the exact moment it's called,
by (1) reading the record's current `Final Video URL` from Airtable — even
if that stored URL is long expired, only the bucket+key portion is reused,
never the signature — and (2) re-signing it with a new 7-day window.

Default behaviour is an HTTP 302 redirect straight to the fresh URL, so
Make's existing "HTTP > Get a file" module needs NO new modules — just point
its URL field at this endpoint (with Allow redirects: Yes, Make's default)
instead of directly at the Airtable-stored link.

  GET /api/video-url?record_id=recLKh7RP1HO5yLrO           -> 302 redirect
  GET /api/video-url?record_id=recLKh7RP1HO5yLrO&format=json -> {"video_url": "..."}

Env vars required (Vercel -> Settings -> Environment Variables):
  AIRTABLE_PAT                  same token already used elsewhere in the project
  REMOTION_AWS_ACCESS_KEY_ID    same as nounda-remotion/.env
  REMOTION_AWS_SECRET_ACCESS_KEY
  REMOTION_AWS_REGION           defaults to us-east-1 if unset
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

import boto3

AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AWS_REGION = os.environ.get("REMOTION_AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("REMOTION_AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("REMOTION_AWS_SECRET_ACCESS_KEY", "")

BASE_ID = "appraw1aDLqrHLY7q"
TABLE_ID = "tbliFOuhLJF1x4CmV"  # Video Production
FIELD_NAME = "Final Video URL"
EXPIRES_IN = 604800  # 7 days, AWS's hard ceiling for SigV4 presigned URLs


def _fetch_stored_url(record_id: str) -> str:
    if not AIRTABLE_PAT:
        raise RuntimeError("AIRTABLE_PAT is not set")
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}/{record_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AIRTABLE_PAT}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Airtable fetch failed: {e.code} {e.read().decode()[:300]}") from e
    stored = (data.get("fields") or {}).get(FIELD_NAME)
    if not stored:
        raise RuntimeError(f"record {record_id} has no '{FIELD_NAME}' value")
    return stored


def _bucket_and_key(stored_url: str):
    """Extract bucket + key from either URL shape S3 has produced for us:
    https://<bucket>.s3.<region>.amazonaws.com/<key>?...  (boto3 virtual-hosted)
    https://<bucket>.s3.amazonaws.com/<key>?...           (legacy virtual-hosted)
    The signature/expiry query params are discarded entirely — only the
    bucket+key survive, which is what makes a "stale" stored URL still usable
    as the source of truth for WHICH FILE to re-sign.
    """
    parsed = urllib.parse.urlparse(stored_url)
    host = parsed.netloc
    bucket = host.split(".s3.")[0].split(".s3")[0]
    key = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not bucket or not key:
        raise RuntimeError(f"could not parse bucket/key from stored URL host={host!r} path={parsed.path!r}")
    return bucket, key


def fresh_url_for(record_id: str) -> str:
    stored = _fetch_stored_url(record_id)
    bucket, key = _bucket_and_key(stored)
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=EXPIRES_IN
    )


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        record_id = (qs.get("record_id") or [""])[0]
        fmt = (qs.get("format") or ["redirect"])[0]

        if not record_id:
            self._json(400, {"error": "missing ?record_id="})
            return
        try:
            url = fresh_url_for(record_id)
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        if fmt == "json":
            self._json(200, {"video_url": url})
        else:
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
