/**
 * GET /api/shortform-backlog — tell the short-form producers whether to make a clip today.
 *
 * SF02, SF03 and SF04 each create a clip on their rotation day whether or not anything is
 * being posted. When the publisher broke in early October production carried on regardless,
 * and the queue filled with clips that were already stale by the time posting resumed.
 * Each producer now calls this first and only continues while fewer than `limit` clips sit
 * in Ready to Post, so production pauses on its own during an outage and restarts once
 * the queue drains.
 *
 * Query: ?limit=N (default 5).
 * Response: { waiting, limit, produce }  where produce = waiting < limit
 */
const BASE_ID = "appraw1aDLqrHLY7q";
const QUEUE_TABLE = "tblbWTsPxCg05mmM2"; // Short-Form Queue
const DEFAULT_LIMIT = 5;

async function countWaiting() {
  const pat = process.env.AIRTABLE_PAT;
  if (!pat) throw new Error("AIRTABLE_PAT is not set");
  let waiting = 0;
  let offset;
  do {
    const qs = new URLSearchParams({ pageSize: "100", filterByFormula: "{Status}='Ready to Post'" });
    qs.append("fields[]", "Status");
    if (offset) qs.set("offset", offset);
    const res = await fetch(`https://api.airtable.com/v0/${BASE_ID}/${QUEUE_TABLE}?${qs}`, {
      headers: { Authorization: `Bearer ${pat}` },
    });
    if (!res.ok) throw new Error(`Airtable GET failed: ${res.status} ${await res.text()}`);
    const page = await res.json();
    waiting += page.records.length;
    offset = page.offset;
  } while (offset);
  return waiting;
}

module.exports = async (req, res) => {
  if (req.method !== "POST" && req.method !== "GET") {
    res.status(405).json({ error: "POST or GET only" });
    return;
  }

  const parsed = parseInt(req.query?.limit, 10);
  const limit = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_LIMIT;

  try {
    const waiting = await countWaiting();
    res.status(200).json({ waiting, limit, produce: waiting < limit });
  } catch (err) {
    // Fail closed: if the count can't be read, skipping one day's clip is the cheap mistake.
    res.status(500).json({ error: String(err.message || err), produce: false });
  }
};
