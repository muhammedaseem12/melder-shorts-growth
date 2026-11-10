// Start a clipping job on the self-hosted open-source worker. No paid clipping API is used.
function isYoutube(value) {
  try {
    const u = new URL(value);
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    return u.protocol === "https:" && ["youtube.com","m.youtube.com","youtu.be"].includes(host);
  } catch { return false; }
}
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });
  const body = req.body || {};
  const url = String(body.videoUrl || "").trim();
  const clipCount = Number(body.clipCount);
  const lengths = new Set(["15-30","30-45","45-60","60-90"]);
  const aspects = new Set(["9:16","1:1","16:9"]);
  let parsed;
  try { parsed = new URL(url); } catch { return res.status(400).json({ error: "Paste a valid HTTPS YouTube or direct video URL." }); }
  if (parsed.protocol !== "https:") return res.status(400).json({ error: "Only secure HTTPS video links are supported." });
  if (![5,10,15,20].includes(clipCount)) return res.status(400).json({ error: "Choose 5, 10, 15 or 20 clips." });
  if (!lengths.has(body.clipLength) || !aspects.has(body.aspectRatio)) return res.status(400).json({ error: "Invalid output settings." });
  if (isYoutube(url) && body.rightsConfirmed !== true) return res.status(400).json({ error: "Confirm that you own this video or have permission to create clips from it." });
  const worker = process.env.CLIPPER_WORKER_URL;
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!worker || !secret) return res.status(503).json({ error: "The free video worker is not configured yet." });
  try {
    const response = await fetch(new URL("/jobs", worker), {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + secret },
      body: JSON.stringify({ videoUrl: url, clipCount, clipLength: body.clipLength, aspectRatio: body.aspectRatio, rightsConfirmed: body.rightsConfirmed === true })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return res.status(response.status >= 400 && response.status < 500 ? response.status : 502).json({ error: data.detail || data.error || "The free worker could not accept this video link." });
    if (!data.jobId) return res.status(502).json({ error: "The video worker returned an invalid job response." });
    return res.status(202).json({ jobId: data.jobId, status: data.status || "queued" });
  } catch {
    return res.status(502).json({ error: "Could not reach the free video worker. It may be waking from sleep; try again shortly." });
  }
};