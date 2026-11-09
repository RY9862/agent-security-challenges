# Turning on Google sign-in for codesafe-ai.cyberuci.com

For whoever runs the server. Ryan has already made the Google OAuth client; this is the part
that happens on the server. About 5 minutes.

## What's already set up (Google Cloud, by Ryan)

- OAuth client (type Web application), client ID
  `223170138274-6m0fvjt41sgrm2pj309i1gs09a2q4jae.apps.googleusercontent.com`
  (this is the script's default; it isn't secret).
- Authorized redirect URI: `https://codesafe-ai.cyberuci.com/api/auth/google/callback`.
  If the site moves to another address, that address's `/api/auth/google/callback` has to be
  added on the client first.
- The **client secret** comes from Ryan privately (not in the repo, a PR or a group chat). It
  usually starts with `GOCSPX-`.

## On the server

From the repo checkout the site runs from (the folder with `web/` in it):

```bash
git fetch origin && git checkout <the branch with Google sign-in> && git pull
web/deploy/enable-google-signin.sh --admins you@uci.edu,someone@gmail.com
```

Paste the client secret when asked. The script:

1. saves it to `web/secrets/google_client_secret`, readable only by the app;
2. sets `ASC_GOOGLE_CLIENT_ID`, `ASC_PUBLIC_URL` (and `ASC_ADMINS` if given; this **replaces**
   the old list, so include everyone who should stay admin) in `web/.env`,
   keeping a backup of the old file;
3. adds `docker-compose.google.yml` (the secret) and rebuilds with `docker compose up -d --build`;
4. checks `https://codesafe-ai.cyberuci.com/api/me` reports `"google":true`.

If the site runs under systemd instead of Docker: `--systemd <service name>`.
Other options: `--client-id`, `--public-url`, `--secret-file`, `--no-restart` (see `--help`).
Running it again is safe; it updates the same settings.

## Who can sign in

- Any Google account. A new account needs the invite code (`ASC_INVITE_CODE`) the first time,
  typed in the box above the Google button, and then picks a username; returning players don't.
- To allow only UCI accounts (no invite code needed), add `ASC_GOOGLE_DOMAINS=uci.edu` to
  `web/.env` and run `docker compose up -d` in `web/`.
- Admins: list Google emails in `ASC_ADMINS`. Google accounts are admins only by email (never
  by username). A non-Gmail admin email (e.g. `@uci.edu`) must be the organisation's Workspace
  account, not a personal Google account registered with that address.
- While the Google consent screen is in **Testing**, only accounts listed as test users on it
  can sign in (Ryan manages that list). Once it's **published**, any Google account can.

## If it doesn't work

| Symptom | Fix |
|---|---|
| Google says `redirect_uri_mismatch` | The URI on the OAuth client must match `ASC_PUBLIC_URL` + `/api/auth/google/callback` exactly (https, no trailing slash). |
| `/api/me` has `"google":false` | The secret didn't load. Check `web/secrets/google_client_secret` exists, `COMPOSE_FILE` in `web/.env` includes `docker-compose.google.yml`, and look for `[asc]` lines in `docker compose logs web`. |
| `/api/me` has no `"google"` key | The old code is still running: check the branch, then rebuild. |
| Google says "Access blocked" / "not a test user" | Ask Ryan to add the account as a test user, or to publish the app. |
| Back on the site, signed out, with no error | The reverse proxy dropped the cookie or the `?state=…&code=…` query. Make sure `/api/auth/google/callback` is passed to the app unchanged. |
| "That sign-in link expired" | Start again from the site's button; a sign-in is valid for 10 minutes and only once. |

To turn it off again: remove `ASC_GOOGLE_CLIENT_ID` and the `COMPOSE_FILE` line from
`web/.env`, then `docker compose up -d` in `web/`. Username/password sign-in keeps working.
