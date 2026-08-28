# User System Architecture

Authentication design for the Adora browser extension. Descriptive only — no environment, deployment, or credential details.

## Overview

Google OAuth-based sign-in. The extension obtains a Google access token, the backend verifies it directly with Google, upserts the user, and issues a short-lived application JWT used for subsequent API calls.

Design goals:
- No passwords stored or handled by Adora
- No long-lived credentials on the client
- Identity provider (Google) remains the source of truth for account verification

## Auth Flow

```
┌──────────────┐    1. Click "Sign in"      ┌────────────────┐
│  Extension   │ ──────────────────────────→│  Google OAuth  │
│  (popup)     │←───────────────────────────│  Consent       │
│              │    2. Google access token  └────────────────┘
│              │
│              │    3. POST /auth/google
│              │       {google_token}
│              │──────────────────────────→ ┌────────────────┐
│              │                            │  Adora API     │
│              │                            │                │
│              │                            │  4. Verify     │
│              │                            │     token with │
│              │                            │     Google     │
│              │                            │     userinfo   │
│              │                            │                │
│              │                            │  5. Upsert     │
│              │                            │     user       │
│              │    6. {access_token, user} │  7. Issue JWT  │
│              │←───────────────────────────│                │
└──────────────┘                            └────────────────┘

Subsequent requests carry a bearer JWT plus the extension API key header.
```

## Data Model

### Table: `users`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | SERIAL | PRIMARY KEY | Internal user ID |
| google_id | TEXT | UNIQUE NOT NULL | Google account identifier (`sub` claim) |
| email | TEXT | UNIQUE NOT NULL | Google account email |
| display_name | TEXT | NOT NULL | Name from Google profile |
| avatar_url | TEXT | | Profile picture URL |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation time |
| last_login | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last successful login |
| is_active | BOOLEAN | DEFAULT TRUE | Account status (moderation hook) |

Indexed on `google_id` and `email`.

**Data minimisation:** only the four profile fields above are stored. No browsing history, no scanned URLs, and no analysis results are linked to a user record.

## API Surface

### POST `/auth/google`
Exchange a Google access token for an Adora JWT.

**Auth:** API key header only (no JWT required)
**Rate limit:** 10 requests/min/IP

Request:
```json
{"google_token": "<google_access_token>"}
```

Response (200):
```json
{
  "user": {
    "id": 1,
    "email": "user@example.com",
    "display_name": "Example User",
    "avatar_url": "https://lh3.googleusercontent.com/..."
  },
  "access_token": "<jwt>"
}
```

Errors: `400` missing token · `401` invalid/expired token or unverified email · `403` deactivated account · `429` rate limited · `500` internal error.

### GET `/auth/me`
Return the current user profile.

**Auth:** API key header + bearer JWT
Errors: `401` missing/invalid/expired JWT.

### POST `/auth/logout`
Client-side logout acknowledgement. Returns `{"ok": true}`.

## JWT Specification

### Claims
| Claim | Value | Purpose |
|-------|-------|---------|
| sub | user ID | Subject |
| email | string | User email |
| iss | `adora-api` | Issuer — blocks cross-service reuse |
| aud | `adora-extension` | Audience — valid only for the extension |
| iat | unix timestamp | Issued at |
| exp | iat + 24h | Expiration |

### Signing & Validation
- Algorithm: HS256, signed with a 64-byte random server-side secret held outside the codebase
- Server validates signature, `iss`, `aud`, and `exp` on every request before trusting `sub`

### Token Lifecycle
1. Sign-in issues a 24-hour JWT
2. Extension stores it in `chrome.storage.local` (isolated per extension)
3. Sent as `Authorization: Bearer <token>` on API calls
4. On `401` the extension silently re-authenticates with Google
5. Logout deletes the token from local storage

No refresh tokens are issued — expiry always forces a fresh identity-provider check.

## Security Model

| Threat | Mitigation |
|--------|-----------|
| Stolen JWT | 24h expiry bounds the window; no long-lived refresh tokens |
| Stolen Google token | Backend re-verifies with Google on each exchange; tokens are short-lived |
| Brute-force login | Rate limiting on the auth endpoint (10/min/IP) |
| Token reuse across services | `iss`/`aud` validation restricts tokens to this extension |
| SQL injection | All queries use parameterized statements |
| XSS token theft | Tokens live only in per-extension isolated storage |
| Network interception | TLS enforced end to end |
| Unauthorized API access | API key required on every endpoint, auth included |
| Credential leakage | All secrets injected from the environment; never committed to the repository |

### Client-Side Token Handling
- Tokens are held only by the background service worker
- Content scripts injected into pages never receive tokens
- The popup communicates with the background worker via message passing, never touching raw tokens

## Privacy Notes
- Adora never receives or stores a Google password
- Only `openid`, `email`, and `profile` scopes are requested
- Accounts can be deactivated server-side without deleting audit fields
