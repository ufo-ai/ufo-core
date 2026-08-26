# Harness handbook — agent guidelines

**This tree is off limits.** Never edit, create, or delete a file under `docs/handbook/`, not even
in the commit that changes the code a page describes. Report the change a page needs instead of
making it.

The pages are generated, not written. `regenerate.sh` runs the pipeline over the current source
and rewrites `overview.md`, `index.md`, `register.md`, and every `stage-*.md` in place;
`.github/workflows/handbook.yml` runs it roughly every 48h and opens the refresh as its own PR.
The next refresh overwrites an edit made here, and until then that PR reports drift the source
never had.
