// Poll the self-hosted open-source clipping worker. No paid third-party provider is used.
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });
  const jobId = req.query && req.query.jobId;
  if (!jobId || typeof jobId !== "string" || !/^[A-Za-z0-9_-]{6,128}$/.test(jobId)) return res.status(400).json({ error: "Invalid job ID." });
  const worker = process.env.CLIPPER_WORKER_URL;
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!worker || !secret) return res.status(503).json({ error: "The free video worker is not configured." });
  try {
    const response = await fetch(new URL("/jobs/" + encodeURIComponent(jobId), worker), { headers: { "Authorization": "Bearer " + secret } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return res.status(response.status === 404 ? 404 : 502).json({ error: data.detail || data.error || "Could not fetch job status." });
    const allowed = new Set(["queued","processing","completed","failed"]);
    if (!allowed.has(data.status)) return res.status(502).json({ error: "Worker returned an unknown job status." });
    const clips = Array.isArray(data.clips) ? data.clips.slice(0,30).map(c => ({
      title: String(c.title || "").slice(0,180), hook: String(c.hook || "").slice(0,280),
      duration: String(c.duration || "").slice(0,24),
      score: Number.isFinite(Number(c.score)) ? Math.max(0,Math.min(100,Number(c.score))) : undefined,
      downloadUrl: typeof c.downloadUrl === "string" && c.downloadUrl.startsWith("/") ? new URL(c.downloadUrl, worker).toString() : undefined
    })) : [];
    return res.status(200).json({ status:data.status, progress:Math.max(0,Math.min(100,Number(data.progress)||0)), message:String(data.message||"").slice(0,300), clips, error:data.status==="failed"?String(data.message||"Processing failed").slice(0,300):undefined });
  } catch {
    return res.status(502).json({ error: "Could not reach the free video worker." });
  }
};