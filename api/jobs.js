// Vercel serverless endpoint. Keep worker credentials server-side only.
const YT_HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"]);
function validYouTubeUrl(value) {
  try {
    const u = new URL(value);
    if (!["https:", "http:"].includes(u.protocol) || !YT_HOSTS.has(u.hostname.toLowerCase())) return false;
    return Boolean(u.searchParams.get("v") || /^\/(shorts|live|embed)\/[A-Za-z0-9_-]{11}/.test(u.pathname) || (u.hostname.includes("youtu.be") && /^\/[A-Za-z0-9_-]{11}/.test(u.pathname)));
  } catch { return false; }
}
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });
  const worker = process.env.CLIPPER_WORKER_URL;
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!worker || !secret) return res.status(503).json({ error: "AI processing is not connected yet. Configure CLIPPER_WORKER_URL and CLIPPER_WORKER_SECRET in the deployment environment, then redeploy." });
  const body = req.body || {};
  if (!validYouTubeUrl(body.videoUrl)) return res.status(400).json({ error: "Provide a valid YouTube video URL." });
  const clipCount = Number(body.clipCount);
  if (![5, 10, 15, 20].includes(clipCount)) return res.status(400).json({ error: "Choose between 5, 10, 15 or 20 clips." });
  const lengths = new Set(["15-30", "30-45", "45-60", "60-90"]);
  const aspects = new Set(["9:16", "1:1", "16:9"]);
  if (!lengths.has(body.clipLength) || !aspects.has(body.aspectRatio)) return res.status(400).json({ error: "Invalid output settings." });
  try {
    const response = await fetch(new URL("/jobs", worker), {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + secret },
      body: JSON.stringify({ videoUrl: body.videoUrl, clipCount, clipLength: body.clipLength, aspectRatio: body.aspectRatio })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return res.status(502).json({ error: data.error || "The AI worker could not accept this job." });
    if (!data.jobId || typeof data.jobId !== "string") return res.status(502).json({ error: "The AI worker returned an invalid job response." });
    return res.status(202).json({ jobId: data.jobId, status: data.status || "queued" });
  } catch (error) {
    return res.status(502).json({ error: "Could not reach the AI processing worker. Check its URL and availability." });
  }
};
