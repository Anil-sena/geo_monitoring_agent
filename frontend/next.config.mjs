/** @type {import('next').NextConfig} */

// The dashboard is exported as a static site and served by the FastAPI process,
// so there is only one server to run. `next dev` still works for UI work; it
// proxies /api to the backend through the rewrite below (rewrites are ignored
// in an export build, which is what we want — there the API is same-origin).
const API = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  output: "export",
  distDir: ".next",
  trailingSlash: true,
  images: { unoptimized: true },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
