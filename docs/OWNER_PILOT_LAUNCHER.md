# Owner pilot launcher

This supports the existing single-owner pilot API on this checkout. It is a local, limited-autonomy pilot—not a production service or live-provider certification. Do not run it against canonical Agency data.

Create an isolated directory with private permissions/ACLs, place `owner.json` directly inside it, and use explicit config values only:

```json
{
  "owner_id": "your-owner-id",
  "subject_id": "your-browser-id",
  "provider": "openai",
  "model": "your-approved-model",
  "base_url": "https://your-approved-endpoint/v1",
  "api_key": "your-key"
}
```

On Windows, the data directory must have a protected ACL limited to its owner, SYSTEM, and Administrators, with private inheritable access; the launcher verifies the real ACL and refuses to repair or weaken unsafe permissions. On POSIX, the directory and files must be owned by the current user and have no group/other permissions. Keep `owner.json` inside the data directory and protect it the same way.

From the checkout root, first start without `--check`:

```text
python -m agency.prototype.pilot_launcher --config <absolute-data-dir>/owner.json --data-dir <absolute-data-dir>
```

On first start the launcher creates `owner.token` exclusively; it prints the token-file path, never the token. Keep that file private. Open `http://127.0.0.1:8766/pilot` in the local browser. Stop with Ctrl+C. Restart by running the same command in a fresh process; existing history and uncertain work are retained and never replayed automatically. A running process holds the owner-store lock, preventing a second launcher from starting against the same data directory.

`--check` is read-only and reports configuration validation only; it does not verify provider availability or provision a token. It deliberately refuses a missing `owner.token` with `token_missing_start_once_without_check`. Therefore the first `--check` before any start is expected to fail; run once without `--check` to create the token, stop the pilot, then use `--check` for later preflight validation:

```text
python -m agency.prototype.pilot_launcher --config <absolute-data-dir>/owner.json --data-dir <absolute-data-dir> --check
```

The launcher accepts only explicit `provider: "openai"` configuration and the existing endpoint validator's HTTPS or literal-loopback HTTP addresses. It does not discover `.env`, use inherited provider settings, start legacy lifecycles, or fall back to echo as a successful pilot response. No live paid/free-provider request is made by qualification tests.
