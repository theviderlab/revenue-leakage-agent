# backend

This file covers `backend/`; the root `CLAUDE.md` and `docs/` apply here. Record decisions as `D#` in `docs/design.md` (rejected alternatives under each one) and implementation gotchas in its Gotchas section.

- Run tests: `make test` from the root, or `uv run pytest` here.
- Lint/format: `uv run ruff check .` and `uv run ruff format .`.
- Add dependencies with `uv add <pkg>` (`uv add --dev <pkg>` for tooling).
- All routes go under the `/api` router in `app/main.py`.
- Settings come from `app/config.py`, which reads the root `.env`.
- Keep `separate_input_output_schemas=False` in `app/main.py`: without it, `make types` emits duplicate `Input` / `Output` types for every model.
- The API contract (`app/schemas.py` and `docs/api.md`) goes first: change it before its consumers. After a contract change, run `make types` so the frontend gets the new types.
