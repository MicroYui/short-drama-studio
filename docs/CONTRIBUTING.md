# Contributing to LTX Desktop

Thanks for taking the time to contribute!

## Getting started (development)

Prereqs:

- Node.js
- `uv` (Python package manager)
- Python 3.12+
- Git

Setup:

```bash
./scripts/pnpm-project.sh setup:dev
```

Run:

```bash
./scripts/pnpm-project.sh dev
```

Debug:

```bash
./scripts/pnpm-project.sh dev:debug
```

Typecheck:

```bash
./scripts/pnpm-project.sh typecheck
```

## What we accept right now

- Bug fixes and small improvements
- Documentation updates
- Small, targeted UI fixes

**Frontend policy:** the frontend is under active refactor. Please avoid large UI/state rewrites for now — open an issue first so we can align on the target direction.

## Proposing larger work

Before starting a larger change (especially frontend architecture/state), please open an issue with:

- The problem you’re trying to solve
- The proposed approach (1–2 paragraphs is fine)
- Scope (areas/files likely to change)
- Any UX or compatibility impact

Wait for maintainer alignment before investing in a major refactor.

## Checks

At minimum, run:

- Type checking:

```bash
./scripts/pnpm-project.sh typecheck
```

- Backend tests:

```bash
./scripts/pnpm-project.sh backend:test
```

On Windows, use `scripts\pnpm-project.ps1` with the same arguments.
