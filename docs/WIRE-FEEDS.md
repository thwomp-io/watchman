# Wire feeds — what they ship beyond the headline

The finance wire reads RSS from two kinds of source, and they differ sharply in what an item carries.
`hn finance wire audit` samples every configured feed (plus the per-ticker Google News and Yahoo feeds
for one ticker) and reports the counts; rerun it after changing `feeds.yaml`.

| Source type | Image | Author | Tags | Outlet | Summary |
|---|---|---|---|---|---|
| Google News search (per ticker, topic lanes, source-scoped wires) | — | — | — | ✓ `<source>` | headline only |
| Yahoo Finance per-ticker RSS | — | — | — | — (link domain) | ✓ |
| Publisher RSS with Media RSS (`media:content` / `media:thumbnail`) | ✓ | often | sometimes | — | ✓ |
| Newsletter platforms (image `<enclosure>`, `content:encoded`) | ✓ | ✓ | — | — | ✓ full body |
| Plain RSS (aggregators, agency feeds) | — | sometimes | sometimes | — | ✓ |

What the harness keeps on every `NewsItem` (each empty when the feed doesn't ship it): `image_url`,
`author`, `tags` (the item's `<category>` values), `publisher` + `publisher_url` (Google News's
`<source>`; its trailing " - Outlet" is cut from the title), `resolved_url` (a Google News link resolved
to the publisher's article — done for catalysts on the standing agent's publish path, cached), and
`filtered` (the catalyst junk rule an item matched).

Single-name catalysts come almost entirely from Google News and Yahoo, which ship no images. The
Inbox reader therefore fetches an article's `og:image` lazily when an item is opened (cached; a failure
just leaves the image out).
