# Setting up "Sign in with Google" (for the maintainer)

Users of Sa'ei sign in with Google through **one shared Google app**, whose
ID lives in [`google_oauth_client.json`](../google_oauth_client.json). This is
done once, by whoever publishes Sa'ei. Until it's filled in, Settings can't
connect Gmail (unless a user adds a client file of their own).

## 1. Create the app (about 10 minutes)

In [Google Cloud Console](https://console.cloud.google.com/), with a project
selected (or a new one named "Sa'ei"):

1. **Turn on the Gmail API.** APIs & Services → Library → *Gmail API* → Enable.
2. **Set up the consent screen.** Google Auth Platform → Branding:
   - App name **Sa'ei**, your support email, and the Sa'ei logo
     (`frontend/assets/logo-128.png`).
   - Audience: **External**.
   - Data Access → Add scopes: `.../auth/gmail.send`, `openid`,
     `.../auth/userinfo.email`.
   - While the app is in **Testing**, add each person who will use it as a
     test user (up to 100).
3. **Create the client.** Google Auth Platform → Clients → Create client →
   Application type **Desktop app**, name "Sa'ei desktop" → Create →
   **Download JSON**.
4. **Copy it into the repo.** Put the downloaded file's `client_id`,
   `client_secret` and `project_id` into `google_oauth_client.json` (or replace
   the file's `"installed"` section with the downloaded one), then commit.

Is it safe to publish? For a Desktop app, Google treats the client secret as
not confidential: an installed app can't keep a secret, so what protects each
user is Google's own consent screen and the token saved on their machine.
GitHub's secret scanning may still flag it on push; that alert can be
dismissed for this file.

## 2. Before it's verified

- Users see **"Google hasn't verified this app"** and must click *Advanced →
  Go to Sa'ei* once.
- At most **100 users**, and in *Testing* only the test users you added.
- In *Testing*, Google ends each sign-in after **7 days**. Switching the
  audience to **In production** (before verification) removes the 7-day limit,
  with the warning screen still shown.

## 3. Verification (step 2, later)

`gmail.send` is a *sensitive* scope, so publishing to everyone without the
warning needs Google's verification: a public homepage, a privacy policy
page, a short video of the sign-in and of an email being sent, and a written
reason for the scope. Submit it from Google Auth Platform → Verification
Center.
