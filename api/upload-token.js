const crypto = require("crypto");
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });
  if (req.headers.origin !== "https://melder-shorts-growth.vercel.app") return res.status(403).json({ error: "Open the official MELDER clipper website to upload." });
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!secret) return res.status(503).json({ error: "Secure upload is not configured. Please contact the site owner." });
  const exp = String(Math.floor(Date.now() / 1000) + 600);
  const sig = crypto.createHmac("sha256", secret).update("upload:" + exp).digest("hex");
  return res.status(200).json({ token: exp + "." + sig });
};
