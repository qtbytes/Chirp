---
name: verify
description: Build, run, and drive the Chirp app (FastAPI backend + React/Vite frontend) to verify a change end-to-end.
---

# Verifying changes in this repo

## Servers

- Backend: `cd backend && uv run uvicorn main:app --port 8000`. The user's own
  instance is often already running on 8000 — probe `http://localhost:8000/docs`
  first; a bind error (WinError 10048) means it's already up, just use it.
- Frontend: `cd frontend && npm run dev` → vite on 5173. If 5173 is taken, the
  user's dev server is already running there and hot-reloads your source edits —
  use it. **Do not drive the app on a fallback port (5174):** the backend only
  allows CORS from 5173, so login silently fails there.

## Driving the UI

`@playwright/test` is a frontend dependency and Chromium is installed. From a
script, resolve it via the frontend package:

```js
import { createRequire } from "node:module";
const require = createRequire("C:/Users/admin/Code/fastapi-learn/twitter_system/frontend/package.json");
const { chromium } = require("@playwright/test");
```

- Create a throwaway account via the API (no email confirmation needed to log in):
  `POST http://localhost:8000/api/v1/auth/register` with
  `{"username","email","password"}` (password ≥ 8 chars).
- Log in through the UI: `getByLabel("Username")`, `getByLabel("Password")`,
  button "Log in", then wait for `.rail-nav`.
- `getByRole("link", { name: "Home" })` is ambiguous (rail brand is
  "Chirp home") — use `exact: true`.
- Test posts land in the dev `twitter.db`; that's accepted, but say so in the
  report.

## Layout breakpoints worth checking for UI changes

- ≤1040px: rail collapses to icons, discovery column hidden.
- ≤720px: rail becomes a fixed bottom bar.
