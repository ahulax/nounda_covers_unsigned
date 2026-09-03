"""B-roll selection: multi-source search, duration-aware scoring, global de-duplication.

Replaces the S23 logic of "run 40 Pexels searches, keep result #1 of each". That
approach was the actual cause of the repetitive/generic b-roll: Pexels ranks by
popularity, so result #1 for a generic phrase is the most-downloaded clip for that
phrase -- the one every other creator also uses -- and two semantically similar
queries return the SAME top clip. It fetched 8 candidates per query and discarded 7.

Here every candidate from every query in a segment competes, clips are scored against
the scene length they have to fill, and a clip already used anywhere in the video is
never offered again. De-duplication happens at SOURCE rather than downstream, so a
segment's pool stays full instead of being thinned after the fact.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")

UA = "nounda-broll/1.0 (+https://nounda.com)"
TIMEOUT = 20
PER_QUERY = 12          # candidates pulled per query per source
DEFAULT_PER_SEGMENT = 8  # clips returned per segment
IDEAL_MIN_SEC = 8.0      # below this a clip visibly loops inside a long scene
IDEAL_MAX_SEC = 30.0     # far beyond a cutaway's useful length

# Ported from nounda-remotion/api/render.ts so filtering happens BEFORE selection
# rather than as a downstream backstop that could only shrink the pool.
URL_BLOCKLIST = [
    "passport", "visa", "id-card", "idcard", "identity-card", "flag", "handshake",
    "child", "children", "kid", "classroom", "vr-", "headset", "gavel", "justice",
    "scale", "stamp", "money", "coin", "cash", "currency", "banknote",
    "6549976", "6538597", "7430215", "7841616",
]
QUERY_BLOCKLIST = [
    "confused", "frowning", "worried", "stressed", "anxious", "frustrated",
    "thinking", "smiling", "satisfied", "happy", "sad", "emotion", "portrait",
]


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return None  # one dead query must not sink the whole batch


def _blocked_url(url):
    u = (url or "").lower()
    return any(w in u for w in URL_BLOCKLIST)


def blocked_query(q):
    s = (q or "").lower()
    return any(w in s for w in QUERY_BLOCKLIST)


def _search_pexels(query):
    if not PEXELS_KEY:
        return []
    qs = urllib.parse.urlencode(
        {"query": query, "per_page": PER_QUERY, "orientation": "landscape", "size": "medium"}
    )
    data = _get_json(f"https://api.pexels.com/videos/search?{qs}", {"Authorization": PEXELS_KEY})
    out = []
    for v in (data or {}).get("videos", []) or []:
        # Pick the largest file at or below 2560px -- 4K files are huge and Remotion
        # gains nothing from them at a 1920x1080 composition.
        files = [f for f in (v.get("video_files") or []) if (f.get("width") or 0) <= 2560]
        if not files:
            continue
        best = max(files, key=lambda f: f.get("width") or 0)
        out.append({
            "source": "pexels",
            "id": f"pexels:{v.get('id')}",
            "url": best.get("link"),
            "width": best.get("width") or 0,
            "height": best.get("height") or 0,
            "duration": float(v.get("duration") or 0),
        })
    return out


def _search_pixabay(query):
    if not PIXABAY_KEY:
        return []
    qs = urllib.parse.urlencode(
        {"key": PIXABAY_KEY, "q": query, "per_page": PER_QUERY,
         "video_type": "film", "safesearch": "true"}
    )
    data = _get_json(f"https://pixabay.com/api/videos/?{qs}")
    out = []
    for v in (data or {}).get("hits", []) or []:
        vids = v.get("videos") or {}
        pick = vids.get("large") or vids.get("medium") or vids.get("small")
        if not pick or not pick.get("url"):
            continue
        out.append({
            "source": "pixabay",
            "id": f"pixabay:{v.get('id')}",
            "url": pick.get("url"),
            "width": pick.get("width") or 0,
            "height": pick.get("height") or 0,
            "duration": float(v.get("duration") or 0),
        })
    return out


def _score(clip, target_sec):
    """Higher is better. Duration dominates: a clip shorter than the scene loops,
    and looping is what reads as 'repetitive' even when every clip is distinct."""
    d = clip["duration"]
    if d <= 0:
        dur = 0.3                      # unknown length, mildly distrusted
    elif d < 4:
        dur = 0.1                      # unusable in a 60-85s scene
    elif d < IDEAL_MIN_SEC:
        dur = 0.35 + 0.4 * (d - 4) / (IDEAL_MIN_SEC - 4)
    elif d <= IDEAL_MAX_SEC:
        dur = 1.0
    else:
        dur = 0.8                      # long is fine, just trimmed

    w = clip["width"]
    res = 1.0 if 1920 <= w <= 2560 else 0.8 if w >= 1280 else 0.45

    # A clip at least as long as the slice it must fill needs no loop at all.
    covers = 1.0 if (target_sec and d >= target_sec) else 0.85
    return dur * 0.6 + res * 0.25 + covers * 0.15


def select_broll(queries, per_segment=DEFAULT_PER_SEGMENT, scene_seconds=None):
    """queries: [{"segment": str, "query": str}]  ->  {"clips": [...], "stats": {...}}

    Every query in a segment contributes candidates to one shared pool, then the pool
    is ranked. This is the key difference from the old pipeline, where each query
    independently committed to its own top hit.
    """
    scene_seconds = scene_seconds or {}
    usable = [q for q in queries if q.get("query") and not blocked_query(q.get("query"))]
    blocked_q = len(queries) - len(usable)

    def run(item):
        q = item["query"]
        return item, _search_pexels(q) + _search_pixabay(q)

    results = []
    if usable:
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(run, usable))

    # Gather candidates per segment, remembering which query produced each clip.
    by_seg = {}
    seen_in_seg = {}
    blocked_url_count = 0
    for item, clips in results:
        seg = item.get("segment") or "unknown"
        bucket = by_seg.setdefault(seg, [])
        seen = seen_in_seg.setdefault(seg, set())
        for c in clips:
            if not c["url"] or _blocked_url(c["url"]):
                blocked_url_count += 1
                continue
            if c["id"] in seen:      # same clip returned by two queries in this segment
                continue
            seen.add(c["id"])
            c = dict(c, query=item["query"])
            bucket.append(c)

    # Rank and assign, keeping a global used-set so no clip appears twice in the video.
    used = set()
    out, stats_seg = [], {}
    for seg in ["intro", "part_1_detail", "part_2_detail", "part_3_detail", "summary"]:
        pool = by_seg.get(seg, [])
        target = float(scene_seconds.get(seg) or 0) / max(per_segment, 1) if scene_seconds.get(seg) else 0
        pool.sort(key=lambda c: _score(c, target), reverse=True)

        # Round-robin across the segment's queries, best-first within each. Ranking the
        # pool purely by score lets one lucky query (whichever happened to return long
        # clips) supply nearly the whole segment -- which reproduces exactly the
        # repetitiveness this is meant to fix. Spreading across queries keeps the
        # SUBJECT matter varied, which is what a viewer actually perceives.
        by_query = {}
        for c in pool:
            by_query.setdefault(c["query"], []).append(c)
        for lst in by_query.values():
            lst.sort(key=lambda c: _score(c, target), reverse=True)

        order = sorted(by_query, key=lambda q: -_score(by_query[q][0], target))
        picked, depth = [], 0
        while len(picked) < per_segment and depth < PER_QUERY * 2:
            progressed = False
            for q in order:
                if len(picked) >= per_segment:
                    break
                lst = by_query[q]
                if depth >= len(lst):
                    continue
                c = lst[depth]
                progressed = True
                if c["id"] in used:
                    continue
                used.add(c["id"])
                picked.append(c)
            if not progressed:
                break
            depth += 1

        # A repeat still beats a black frame, so backfill a thin segment rather than
        # under-filling it -- but only after every distinct clip has been exhausted.
        if not picked and pool:
            picked = pool[:1]

        stats_seg[seg] = {"candidates": len(pool), "picked": len(picked)}
        for c in picked:
            out.append({
                "segment": seg,
                "query": c["query"],
                "video_url": c["url"],
                "duration": c["duration"],
                "width": c["width"],
                "source": c["source"],
            })

    return {
        "clips": out,
        "stats": {
            "queries_in": len(queries),
            "queries_blocked": blocked_q,
            "clips_blocked_by_url": blocked_url_count,
            "total_candidates": sum(len(v) for v in by_seg.values()),
            "returned": len(out),
            "distinct": len({c["video_url"] for c in out}),
            "sources": {"pexels": bool(PEXELS_KEY), "pixabay": bool(PIXABAY_KEY)},
            "per_segment": stats_seg,
        },
    }
