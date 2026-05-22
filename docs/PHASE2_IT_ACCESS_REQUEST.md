# Phase 2 — IT / Microsoft 365 Access Request

> **For:** Irish Homes IT administrator (Microsoft 365 / Entra ID Global Admin)
> **From:** [your name]
> **Re:** Access for the MTR Batch Review agent to read submission documents from
> SharePoint and write back control sheets.
>
> Forward the section below to IT. Square-bracketed items are for you/IT to fill in.

---

## What we're doing (1 paragraph)

We've built an internal tool that reviews Mortgage-to-Rent (MTR) submission
batches — it reads the documents for each property, cross-checks key fields, and
produces a colour-coded (Red/Amber/Green) control sheet. Today staff upload the
files manually. In Phase 2 we want it to run automatically: **watch a designated
SharePoint folder, pick up new submission documents, review them, and write the
control sheets back to an output folder.** To do that securely it needs an
app-only (unattended) connection to Microsoft Graph, scoped to **one specific
SharePoint site only**.

---

## What we need IT to set up

1. **Register an application** in Microsoft Entra ID (Azure AD):
   *Entra admin centre → App registrations → New registration.*
   - Name: `MTR Batch Review Agent`
   - Account type: **Single tenant** (this organisation only)
   - No redirect URI needed (it's a background service, not interactive).

2. **Create a credential** for it (app-only authentication):
   - Preferred: a **client secret** (24-month expiry) — *Certificates & secrets → New client secret*.
   - Or, if policy requires, a **certificate** (we can provide a CSR/public key).

3. **Grant Microsoft Graph *application* permissions** — least privilege:

   | Permission | Type | Why | Least-privilege note |
   |---|---|---|---|
   | `Sites.Selected` | Application | Read **and** write files in **only** the SharePoint site(s) you explicitly authorise | **Strongly preferred** — grants access to nothing until an admin grants this app rights on the specific site below |

   With `Sites.Selected`, after consent an admin grants this app **`read` + `write`**
   on the specific site using the Graph "site permissions" grant (or the
   `Grant-PnPAzureADAppSitePermission`/Graph `POST /sites/{id}/permissions` call).
   This avoids tenant-wide file access.

   *(If `Sites.Selected` can't be used, the broader fallbacks are
   `Sites.ReadWrite.All`. We'd rather not — `Sites.Selected` is the secure option.)*

4. **Admin-consent** the permission (application permissions require Global Admin
   or Privileged Role Admin consent — the "Grant admin consent" button).

5. **Designate the SharePoint location** the agent should use:
   - Site: `[SharePoint site URL, e.g. https://irishhomes.sharepoint.com/sites/MTR]`
   - Document library: `[e.g. "Submissions"]`
   - Inbox folder (agent reads new files here): `[e.g. /Inbox]`
   - Output folder (agent writes control sheets here): `[e.g. /Reviewed]`
   - Grant this app **read + write** on that site (per step 3).

---

## What to send back to us (securely — not by plain email)

Please return these via [1Password / Keeper / your secrets manager]:

- **Directory (tenant) ID**
- **Application (client) ID**
- **Client secret value** (or certificate)
- **SharePoint site URL** (and confirmation the app was granted read+write on it)

We store the secret only as an encrypted environment variable in the hosting
platform — never in source control.

---

## Data-protection note (please review)

The agent sends document text/images to **Anthropic's Claude API** for field
extraction. This processes personal and financial data (borrower names, addresses,
valuations), so it should be reviewed under GDPR before go-live:

- Anthropic offers a **Data Processing Addendum (DPA)** and a **zero-data-retention**
  option for API traffic (data not stored or used for training). We can request
  these for our account.
- Suggested action: DPO/IT to confirm this is acceptable and that a DPA is in place.

---

## Networking (only if relevant)

The agent runs on our hosting platform (currently Render). If IT enforces an
**IP allowlist / Conditional Access** on SharePoint/Graph, we'll provide the
service's **static outbound IP addresses** to allowlist. Let us know if this applies.

---

## Appendix — technical detail (for IT, optional reading)

- **Auth flow:** OAuth 2.0 **client credentials** (app-only), token endpoint
  `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`, scope
  `https://graph.microsoft.com/.default`.
- **How it reads files:** Graph **delta queries** on the library drive
  (`/drives/{id}/root/delta`) to detect new files — polling, no inbound webhook
  required. (If you'd prefer change-notification webhooks, we can host the
  notification endpoint instead.)
- **What it touches:** lists/reads files in the Inbox folder; creates files in the
  Output folder; optionally moves processed files to a "Done" subfolder. It does
  **not** need user mailboxes, calendars, Teams, or directory data.
- **No standing infrastructure in your tenant** beyond the app registration.
