# frontend

This file covers `frontend/`; the root `CLAUDE.md` and `docs/` apply here. Record decisions as `D#` in `docs/design.md` (rejected alternatives under each one) and implementation gotchas in its Gotchas section.

- Check: `npm run typecheck` and `npm run lint` here, or `make lint` from the root.
- Tests: `npm test` here, or `make test` from the root.
- Add dependencies with `npm install <pkg>`.
- Call the backend through `api()` in `src/lib/api.ts`; paths are relative to `/api`.
- The API contract (the backend OpenAPI schema and `docs/api.md`) is the shared source of truth. API types come from `make types` into `src/lib/api-types.ts` and are never written by hand.
- Work against the mock implementation with `make dev AGENT_BACKEND=mock` (no API key needed).
