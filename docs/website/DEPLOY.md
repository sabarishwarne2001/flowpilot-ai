# Deploying the website (servabo.app)

The website in `website/` is plain static files (HTML, CSS, images, fonts): no server, no database,
no third-party scripts. It is built by `npm run build` into `website/dist`. Two ways to put it
online:

- **Option A — Cloudflare Pages (recommended, free).** Cloudflare builds it from GitHub on every
  push to `main` and serves it worldwide with HTTPS.
- **Option B — Caddy on the app server.** The same server that runs the app also serves the site.

Either way the site needs four settings (build-time environment variables, all optional; the
defaults are in `website/src/config/site.ts`):

| Variable | Default | What it is |
|---|---|---|
| `PUBLIC_SITE_URL` | `https://servabo.app` | The website's address (canonical links, sitemap, Open Graph) |
| `PUBLIC_APP_URL` | `https://app.servabo.app` | The app ("Sign in", "Start free trial") |
| `PUBLIC_CONTACT_EMAIL` | `sabarishwarne@gmail.com` | The public contact address; switch to `support@servabo.app` once email forwarding works |
| `PUBLIC_SUPPORT_PHONE` | empty | Optional. When set, shown on the contact page only. Never commit it. |

## Option A — Cloudflare Pages, step by step

You need a Cloudflare account (free) with your domain added to it (buying the domain at Cloudflare
Registrar does that automatically).

1. **Connect GitHub.** In the Cloudflare dashboard: **Workers & Pages → Create → Pages → Connect to
   Git**. Sign in to GitHub when asked and choose **Only select repositories → the Servabo
   repository** (it can stay private: Cloudflare gets read access to this one repository only).
2. **Pick the repository** and click **Begin setup**.
3. **Build settings:**
   - Project name: `servabo` (the preview address becomes `servabo.pages.dev`)
   - Production branch: `main`
   - Framework preset: **Astro**
   - Build command: `npm run build`
   - Build output directory: `dist`
   - **Root directory (advanced): `website`**
4. **Environment variables (advanced) → Production:** add
   - `NODE_VERSION` = `22.12.0`
   - `PUBLIC_SITE_URL` = `https://servabo.app`
   - `PUBLIC_APP_URL` = `https://app.servabo.app`
   - `PUBLIC_CONTACT_EMAIL` = `sabarishwarne@gmail.com` (later `support@servabo.app`)
   - `PUBLIC_SUPPORT_PHONE` only if you want a phone number on the contact page
5. **Save and Deploy.** The first build takes about two minutes. Open the `*.pages.dev` address it
   shows and click through: Home, Pricing, the footer's legal pages.
6. **Custom domains.** In the project: **Custom domains → Set up a custom domain** →
   `servabo.app` → **Activate domain** (Cloudflare adds the DNS record itself). Do the same for
   `www.servabo.app`.
7. **Send www to the main address.** **Rules → Redirect Rules → Create rule → template "Redirect
   from WWW to root"** for `servabo.app`, status 301, keep the query string. Deploy.
8. **Check it.** In a terminal:
   ```bash
   curl -sI https://servabo.app/pricing | grep -iE "^HTTP|content-security-policy|strict-transport"
   curl -sI https://www.servabo.app | grep -iE "^HTTP|location"
   ```
   The first shows `200`, the Content-Security-Policy and HSTS; the second a `301` to
   `https://servabo.app/`.

Every push to `main` now redeploys the site. A pull request gets its own preview address.

Cost: free (Cloudflare Pages' free plan allows 500 builds a month).

## Option B — Caddy on the app server

1. Build on any machine with Node 22: `cd website && npm ci && npm run build`, with the four
   variables above exported first if you need non-default values. Copy `website/dist` to the
   server, for example to `/srv/servabo/website/dist`.
2. In `backend/docker-compose.prod.yml`, give the `caddy` service two more read-only mounts:
   `../website/dist:/srv/website:ro` and `./deploy/Caddyfile.website:/etc/caddy/Caddyfile.website:ro`,
   and the environment variable `WEBSITE_DOMAIN: servabo.app`.
3. Add this line at the end of `backend/deploy/Caddyfile`: `import /etc/caddy/Caddyfile.website`.
4. DNS: `A` records for `servabo.app` and `www.servabo.app` pointing at the server.
5. `docker compose -f docker-compose.prod.yml up -d caddy`. Caddy gets the certificates itself.

`backend/deploy/Caddyfile.website` sends exactly the headers of `website/public/_headers`, redirects
`www` to the main address, serves `/pricing` from `pricing.html` and answers unknown paths with the
site's 404 page.

## Switching to the fallback domain (getservabo.com)

If `servabo.app` was taken when you bought the domain, use `getservabo.com` for the website and
`app.getservabo.com` for the app. The switch is the settings, nothing in the code:

- Website: `PUBLIC_SITE_URL=https://getservabo.com` and `PUBLIC_APP_URL=https://app.getservabo.com`
  (Cloudflare Pages: Settings → Environment variables, then redeploy), and the custom domains in
  step 6 above.
- App (`backend/.env.production`): `APP_DOMAIN=app.getservabo.com`,
  `FRONTEND_URL=https://app.getservabo.com`, `CORS_ORIGINS=https://app.getservabo.com`,
  `WEBSITE_URL=https://getservabo.com`, and `PLATFORM_RESERVED_HOSTS` with the two new names
  (RUNBOOK §9).

## Analytics (optional, later)

The site runs no analytics. To add Cloudflare's cookieless Web Analytics: in Cloudflare, **Analytics
& Logs → Web Analytics → Add a site** (choose the automatic setup for a Pages project, which needs no
code), or paste its beacon where the comment in `website/src/layouts/Base.astro` says and allow
`https://static.cloudflareinsights.com` in `script-src` and `https://cloudflareinsights.com` in
`connect-src` in `website/public/_headers` (and `Caddyfile.website`). Then update the privacy and
cookies pages, which today say the site runs no analytics.
