# nok-posting

Publishes the @noknyc posts that sit in `queue/` through the Instagram API. See the top of `publish.py` for the queue entry format. Tests: `python -m pytest -q -p no:cacheprovider`.

## Trial Reels (optional, off by default)

A Trial Reel is shown first to people who do not follow the account. Meta describes it in the Content Publishing guide, section "Publish Trial Reels Post".

To post one, add a `trial` field to a **REELS** queue entry:

```json
{"publish_at": "2026-10-02T12:30:00Z", "type": "REELS", "file": "media/rugs.mp4",
 "caption": "...", "approved": "<owner's words>", "trial": "MANUAL"}
```

| `trial` value      | What it asks Meta for                                              |
|--------------------|--------------------------------------------------------------------|
| *(field missing)*  | A normal Reel. The request is exactly the same as before this feature. |
| `MANUAL`           | Trial Reel; you graduate it to followers yourself in the app.      |
| `SS_PERFORMANCE`   | Trial Reel; Instagram graduates it automatically if it performs well. |

How it works: the publisher adds `trial_params={"graduation_strategy": "<value>"}` (JSON-encoded) to the REELS container create call. Nothing else changes.

Rules:
- Any other value (wrong case, empty, a number) is rejected, and so is `trial` on an IMAGE or STORIES entry. The entry fails with a clear message, nothing is sent to Meta, and the entry stays in `queue/`.
- `"trial": null` counts as off.
- Meta may restrict Trial Reels to some accounts. This has not been tried against the live API.
