/**
 * POST /api/broll-dedup — keep the B-Roll Bank to one Active record per real clip.
 *
 * SF01/SF01b create a record for every Pexels search hit without checking whether that
 * clip is already banked, so every top-up run re-inserts the same videos. By 2026-09-30
 * the bank reported 257 Active clips but held only 88 distinct videos, some stored nine
 * times under the same tag. That inflation is not cosmetic: a single Format B reel drew
 * the same footage into two of its four beats, because the pool it sampled was mostly
 * copies of each other.
 *
 * Also retires clips whose Pexels thumbnail slug names somewhere that is plainly not
 * France. This is a backstop, not the main defence -- the vision gate on SF01/SF01b is --
 * but a Costa Rican government building did reach a published reel, and its flag is a
 * horizontal red/white/blue that reads as French at a glance, so a cheap second net is
 * worth having.
 *
 * Response: { scanned, retired_duplicates, retired_foreign, active_after, per_tag }
 */
const BASE_ID = "appraw1aDLqrHLY7q";
const TABLE_ID = "tblNDxPvd8EWZbKDL"; // B-Roll Bank

// Slugs seen in real mistagged clips plus the obvious neighbours. Matched against the
// Pexels thumbnail filename, which usually carries the uploader's own location words.
const NOT_FRANCE = [
  "costa-rica", "istanbul", "baku", "chapultepec", "mexico", "moscow", "dubai",
  "new-york", "london", "berlin", "madrid", "lisbon", "rome", "amsterdam", "warsaw",
  "budapest", "prague", "vienna", "brussels", "zurich", "geneva", "barcelona",
];

// A single run should never be able to gut the bank. If a change ever makes the rule
// match far more than expected, the cap turns a disaster into a visible anomaly.
const MAX_RETIRE_PER_RUN = 250;

async function airtable(path, init = {}) {
  const pat = process.env.AIRTABLE_PAT;
  if (!pat) throw new Error("AIRTABLE_PAT is not set");
  const res = await fetch(`https://api.airtable.com/v0/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${pat}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!res.ok) throw new Error(`Airtable ${init.method || "GET"} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function fetchAll() {
  const out = [];
  let offset;
  do {
    const qs = new URLSearchParams({ pageSize: "100" });
    if (offset) qs.set("offset", offset);
    const page = await airtable(`${BASE_ID}/${TABLE_ID}?${qs}`);
    out.push(...page.records);
    offset = page.offset;
  } while (offset);
  return out;
}

async function retire(ids) {
  for (let i = 0; i < ids.length; i += 10) {
    await airtable(`${BASE_ID}/${TABLE_ID}`, {
      method: "PATCH",
      body: JSON.stringify({
        records: ids.slice(i, i + 10).map((id) => ({ id, fields: { Status: "Retired" } })),
      }),
    });
  }
}

module.exports = async (req, res) => {
  if (req.method !== "POST" && req.method !== "GET") {
    res.status(405).json({ error: "POST or GET only" });
    return;
  }

  try {
    const all = await fetchAll();
    const active = all.filter((r) => r.fields.Status === "Active");

    const foreign = active.filter((r) => {
      const thumb = String(r.fields["Thumbnail URL"] || "").toLowerCase();
      return NOT_FRANCE.some((slug) => thumb.includes(slug));
    });
    const foreignIds = new Set(foreign.map((r) => r.id));

    // Keep the most-used copy of each clip so its usage history survives, then the oldest.
    const byPexelsId = new Map();
    for (const r of active) {
      if (foreignIds.has(r.id)) continue;
      const pid = String(r.fields["Pexels ID"] || "").trim();
      if (!pid) continue;
      if (!byPexelsId.has(pid)) byPexelsId.set(pid, []);
      byPexelsId.get(pid).push(r);
    }

    const duplicateIds = [];
    const keep = [];
    for (const group of byPexelsId.values()) {
      group.sort(
        (a, b) =>
          (b.fields["Used Count"] || 0) - (a.fields["Used Count"] || 0) ||
          a.createdTime.localeCompare(b.createdTime)
      );
      keep.push(group[0]);
      duplicateIds.push(...group.slice(1).map((r) => r.id));
    }

    const toRetire = [...foreignIds, ...duplicateIds];
    if (toRetire.length > MAX_RETIRE_PER_RUN) {
      res.status(409).json({
        error: `refusing to retire ${toRetire.length} records in one run (cap ${MAX_RETIRE_PER_RUN})`,
        duplicates: duplicateIds.length,
        foreign: foreignIds.size,
      });
      return;
    }
    if (toRetire.length) await retire(toRetire);

    const perTag = {};
    for (const r of keep) {
      const tag = r.fields["Mood / Subject Tag"] || "(untagged)";
      perTag[tag] = (perTag[tag] || 0) + 1;
    }

    res.status(200).json({
      scanned: all.length,
      retired_duplicates: duplicateIds.length,
      retired_foreign: foreignIds.size,
      active_after: keep.length,
      per_tag: perTag,
    });
  } catch (err) {
    res.status(500).json({ error: String((err && err.message) || err) });
  }
};
