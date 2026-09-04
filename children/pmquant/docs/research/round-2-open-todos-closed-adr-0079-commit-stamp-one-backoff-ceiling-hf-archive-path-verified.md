Verdict: the build's open TODOs are closed except the four that need an owner ruling; round 2 fixed three more real defects found by exercising real code paths.

## What closed

- **`acquired_at` is the commit instant (ADR-0079).** The platform used to stamp before `read()` and refuse any observation dated after that stamp, so every "now"-dated capture stream raced it. Rows now stream to staging under a placeholder, the stamp is taken once after the read is exhausted, the ADR-0014 assertion compares against it, and the staged members are restamped line by line (byte-identical apart from the stamp, pinned) before verify, manifest, WORM rename and the last-of-all checkpoint.
- **One backoff ceiling.** `connector.MAX_BACKOFF_S` (60 s) caps every single wait — server-sent `Retry-After` included — in `kalshi`, `predexon`, `polymarket`, `restapi` and `schwab`; identity-pinned across all five so a local restatement fails.
- **The Hugging Face archive path is exercised for real.** `huggingface_hub` is installed, the real `download()` body is tested offline (exact kwargs, the hub's 404 mapping, an outage, a 401, the token never in text), and the pack was probed once against the live dataset `phobia76/pmxt-l2-dump` (anonymous access through the container proxy; the small `meta/` object; an absent path returns `None`). The default path pattern renders the repository's real layout; hour files are about 360 MB and stream in batches.
- `alpaca_quotes` registered and in every tree; the `all` extra carries `huggingface_hub`.

## Defects found and fixed in round 2

- A hub OUTAGE read as an absent hour: `LocalEntryNotFoundError` is an `EntryNotFoundError`, so `download()` returned `None` and the pull logged "step over this hour". It now refuses by name; the hub's own 404 (`RemoteEntryNotFoundError`, which is also an `HfHubHTTPError`) still maps to `None`, and the clause order is pinned by a swap-proof test.
- `Retry-After: nan` reached `time.sleep(nan)` in the Polymarket pack; now unusable and falls back to the exponential backoff.
- `restapi` and `schwab` doubled their backoff without any ceiling; both now cap at the shared constant.

## Still owner rulings

The torch pack's SGD default (a declared-model kind could REQUIRE `optimizer`); Polymarket early resolution (a closed market whose `end_date` is ahead is refused as a future observation — kind is declared by the venue's flag, never by date); the child's two frozen-recipe items (the MIO's separable in-program fee, the panel tail features seeing the eventual rung set); `utc_now`'s second truncation under ADR-0079.
