# Development

```bash
python3 -m unittest discover -s tests          # CLI against a fake gh and real temp git repos
python3 tests/run_parallel.py                  # the same tests, one process per test class (what CI runs; much faster)
python3 -m ruff check scripts tests            # lint (ruff.toml)
scripts/gen-cli-docs                           # regenerate docs/cli.md after changing the command table
claude plugin test .                           # mod tests (hooks/*.test.ts); needs a Claude Code build that knows mods
                                               # (validating the plugin itself: point `claude plugin validate` at a copy without marketplace.json;
                                               # builds older than ~2.1.280 warn about `types`/`modules` and skip the band)
```

The unit tests use a fake gh, so the real GraphQL schema is not covered. `CGP_LIVE_TEST=1 CGP_LIVE_BOARD=<board url> python3 tests/live_smoke.py` runs the read-only commands against a throwaway board with your real token. A live agent run is not covered either: try a throwaway board first.

## Releases

Versions are automated with [release-please](https://github.com/googleapis/release-please). PR titles must be [conventional commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `feat!:` ...; checked in CI) because squash merges use the title as the commit message. Merging to `main` keeps a release PR open that bumps `.claude-plugin/plugin.json` and `CHANGELOG.md`; merging that PR tags `vX.Y.Z` and publishes a GitHub release, which is what installed plugins update to. Pin a release with `claude plugin marketplace add kelsin/claude-github-project#vX.Y.Z`.

See [Architecture](architecture.md) for the code layout.
