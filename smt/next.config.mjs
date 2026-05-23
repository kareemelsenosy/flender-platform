/** @type {import('next').NextConfig} */
const nextConfig = {
  // SMT is served under /smt on the shared ordersheet.flendergroup.com domain.
  // Next.js auto-prefixes page routes, <Link> URLs, and static assets with this.
  // Raw fetch() calls are NOT auto-prefixed — use the apiFetch() helper from
  // lib/api.ts (which reads BASE_PATH from the same place).
  // To move SMT onto its own subdomain later, set both this and BASE_PATH to ''.
  basePath: '/smt',
};

export default nextConfig;
