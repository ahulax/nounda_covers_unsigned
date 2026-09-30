/**
 * POST /api/shortform-hashtags — fill the per-platform hashtag fields on Short-Form Queue
 * drafts, so SF06 has them ready when a clip is approved for publishing.
 *
 * The long-form social lane already appends cluster hashtags to its captions, pulling them
 * from the SEO & Social Reference table keyed on the article's Cluster. Short-form records
 * can reach the same table through their Source Article link, so the tags come for free.
 *
 * This lives in code rather than as a chain of Airtable modules in Make for a practical
 * reason: scenarios built through the Make API arrive with no connection attached and no
 * way to attach one programmatically, so every module has to be reconfigured by hand. As
 * one HTTP call there is nothing to configure and nothing to keep in sync.
 *
 * Instagram gets a trimmed list. The long-form posts carry 15-20 tags, which is fine on a
 * feed post but reads as spam under a Reel, so only the leading, most specific tags are
 * kept. Facebook and LinkedIn take the curated list as-is, matching what already works.
 *
 * Response: { scanned, enriched, skipped_no_article, skipped_no_cluster_row, details }
 */
const BASE_ID = "appraw1aDLqrHLY7q";
const QUEUE_TABLE = "tblbWTsPxCg05mmM2"; // Short-Form Queue
const BLOG_TABLE = "tbls1wPrzp4gRnHOs"; // Blog Posts
const REFERENCE_TABLE = "tbliFixgxn1ZfRYj1"; // SEO & Social Reference

const INSTAGRAM_TAG_LIMIT = 7;
const MAX_PER_RUN = 25;

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

function trimTags(raw, limit) {
  const tags = String(raw || "").trim().split(/\s+/).filter(Boolean);
  return tags.slice(0, limit).join(" ");
}

module.exports = async (req, res) => {
  if (req.method !== "POST" && req.method !== "GET") {
    res.status(405).json({ error: "POST or GET only" });
    return;
  }

  try {
    // Deliberately not restricted to Draft. Approval can happen minutes after a clip is
    // rendered, well inside any polling interval, and a record approved before the first
    // scan would otherwise never be enriched at all. Anything not yet published and still
    // missing tags is fair game; Posted and Rejected are left alone.
    const formula =
      "AND({Instagram Hashtags}='', {Status}!='Posted', {Status}!='Rejected')";
    const qs = new URLSearchParams({ filterByFormula: formula, pageSize: String(MAX_PER_RUN) });
    const queue = await airtable(`${BASE_ID}/${QUEUE_TABLE}?${qs}`);

    // One lookup of the reference table serves every record in the batch.
    const reference = await airtable(`${BASE_ID}/${REFERENCE_TABLE}?pageSize=100`);
    const byCluster = new Map();
    for (const r of reference.records) {
      const cluster = String(r.fields.Cluster || "").trim();
      if (cluster) byCluster.set(cluster, r.fields);
    }

    const details = [];
    let enriched = 0;
    let skippedNoArticle = 0;
    let skippedNoClusterRow = 0;

    for (const record of queue.records) {
      const articleIds = record.fields["Source Article"] || [];
      if (articleIds.length === 0) {
        skippedNoArticle++;
        details.push({ id: record.id, skipped: "no Source Article link" });
        continue;
      }

      const article = await airtable(`${BASE_ID}/${BLOG_TABLE}/${articleIds[0]}`);
      const cluster = String(article.fields.Cluster || "").trim();
      const row = byCluster.get(cluster);
      if (!row) {
        // Blog Posts carries cluster names the reference table has no row for, so this is
        // an expected gap rather than a fault. Leaving the fields blank is the safe
        // outcome: SF06 simply publishes without hashtags instead of with wrong ones.
        skippedNoClusterRow++;
        details.push({ id: record.id, skipped: `no reference row for cluster "${cluster}"` });
        continue;
      }

      await airtable(`${BASE_ID}/${QUEUE_TABLE}/${record.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          fields: {
            "Instagram Hashtags": trimTags(row["Instagram Hashtags"], INSTAGRAM_TAG_LIMIT),
            "Facebook Hashtags": String(row["Facebook Hashtags"] || "").trim(),
            "LinkedIn Hashtags": String(row["LinkedIn Hashtags"] || "").trim(),
          },
        }),
      });
      enriched++;
      details.push({ id: record.id, cluster });
    }

    res.status(200).json({
      scanned: queue.records.length,
      enriched,
      skipped_no_article: skippedNoArticle,
      skipped_no_cluster_row: skippedNoClusterRow,
      details,
    });
  } catch (err) {
    res.status(500).json({ error: String((err && err.message) || err) });
  }
};
