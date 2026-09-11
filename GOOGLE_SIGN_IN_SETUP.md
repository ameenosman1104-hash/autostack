# Google sign-in setup

Create a Google Cloud OAuth client of type **Web application** and configure the consent screen for the intended audience. Publish the consent screen when ready; testing mode requires listed test users.

Set these server environment variables (never commit credentials):
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- GOOGLE_REDIRECT_URI=https://www.autostack.co.za/auth/google/callback
- SECRET_KEY: a stable, private session signing key, identical across workers.

Add the exact redirect URI above to the Google client’s Authorized redirect URIs. For a separate Render-domain login, register that domain’s exact callback and configure GOOGLE_REDIRECT_URI accordingly. Start and finish the flow on the same origin; do not mix www, bare domain, and Render origins. Local development may use http://localhost:5000/auth/google/callback.

The login button appears only when all three Google settings are configured. Google proves identity using OpenID Connect. Authlib validates state/nonce and the ID token; identity matching uses Google sub, not mutable email. Tokens are not persisted.

Existing accounts require their AutoStack password once to link; matching emails are never automatically linked. New users confirm business name and username. Blocked accounts remain blocked. Username/password login is unchanged. Google login does not authorize Gmail sending or replace SMTP App Password configuration.

Database: an additive google_identities table in the existing main database maps unique Google subjects to unique tenant IDs. No existing users, passwords or financial records are converted. Removing the feature does not require deleting any customer records.

Live verification: consent redirect, cancellation, existing-account link, new account, repeated Google login, blocked account, restart, and original password login. Automated tests mock Google; they cannot prove live consent/client configuration.
