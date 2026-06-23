"""Write a `{dest}/lodging/` report from a HotelSearch — the deepen-after-pick artifact.

Turns live Google-Hotels results into a corpus lodging report: per-property block with the prose
blurb, nearby places + travel times, excluded amenities, deal signal, locally-downloaded photos, and
the booking link. Non-destructive: refuses to overwrite a human-edited report without `force`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from harness.travel.media import dest_dir_parts, store_image
from harness.travel.models import HotelOffer, HotelSearch, ImageCandidate


def _property_block(o: HotelOffer, photo_embeds: list[str]) -> list[str]:
    stars = f"{o.hotel_class}★ · " if o.hotel_class else ""
    rating = ""
    if o.overall_rating:
        rating = f"{o.overall_rating}★"
        if o.reviews:
            rating += f" ({o.reviews:,} reviews)"
    price = f"**${o.price_per_night_usd:,.0f}/night**" if o.price_per_night_usd else "price n/a"
    total = f" · ${o.total_usd:,.0f} total" if o.total_usd else ""
    deal = f"  ·  ⭐ {o.deal}" if o.deal else ""
    out = [f"## {o.name}", f"*{stars}{rating} · {price}{total}*{deal}", ""]
    if o.description:
        out += [o.description, ""]
    out += photo_embeds
    if photo_embeds:
        out.append("")
    if o.nearby_places:
        out.append(
            "**Nearby**: "
            + " · ".join(
                f"{np.name} ({np.duration} {np.transport})".strip().replace("()", "")
                for np in o.nearby_places[:5]
            )
        )
    if o.excluded_amenities:
        out.append(f"**Lacks**: {', '.join(o.excluded_amenities)}")
    if o.amenities:
        out.append(f"**Amenities**: {', '.join(o.amenities[:8])}")
    if o.booking_link:
        out.append(f"**[Book →]({o.booking_link})**")
    out.append("")
    return out


def write_lodging_report(
    search: HotelSearch,
    dest: str,
    vault_root: Path,
    *,
    photos_per: int = 5,
    force: bool = False,
    researched_on: date | None = None,
) -> Path:
    """Write `{dest}/lodging/{check_in}.md` (+ download photos to `lodging/{check_in}-assets/`) —
    window-stamped so a destination's `lodging/` dir accretes a price/trend time-series across trips.
    `dest` is a bare destination slug (resolved wherever filed) or a vault path under travel/.
    `researched_on` (default today) stamps the snapshot's capture date — essential for trend value.
    Returns the report path. Non-destructive: raises FileExistsError if that window's report exists
    and `force` is False."""
    stamped = researched_on or date.today()
    folder_parts = dest_dir_parts(dest, vault_root)
    lodging_dir = vault_root.joinpath(*folder_parts, "lodging")
    win = search.check_in.isoformat()
    report = lodging_dir / f"{win}.md"
    if report.exists() and not force:
        raise FileExistsError(f"{report} already exists — pass force=True to overwrite")
    lodging_dir.mkdir(parents=True, exist_ok=True)
    # store_image dest: a "/"-path under travel/ → travel/{...}/lodging/{win}-assets (per-window assets)
    lodging_dest = "/".join((*folder_parts[1:], "lodging", f"{win}-assets"))

    cache = " · _from cache_" if search.from_cache else ""
    lines: list[str] = [
        f"# Lodging — {search.location} · {search.check_in} → {search.check_out}",
        "",
        f"> Live Google-Hotels research for **{search.check_in} → {search.check_out}** "
        f"({search.nights}n){cache}. **Researched {stamped.isoformat()}** — a price snapshot "
        f"(fares drift; re-run `travel hotels` near booking). Booking stays manual (the gated escalation).",
        "",
    ]
    for o in search.offers:
        # full bank, exact-dup-deduped, capped at photos_per — the hotel's curated photo set
        urls = list(dict.fromkeys(o.image_urls or ([o.image_url] if o.image_url else [])))[:photos_per]
        embeds: list[str] = []
        for idx, url in enumerate(urls):
            subject = o.name if idx == 0 else f"{o.name} {idx + 1}"
            try:
                cand = ImageCandidate(
                    subject=subject,
                    title=o.name,
                    image_url=url,
                    source="google_hotels",
                    source_url=o.booking_link,
                    attribution=f"via Google Hotels: {o.name}",
                )
                res = store_image(cand, vault_root, lodging_dest, subject)
                embeds.append(res.markdown(560 if idx == 0 else 320))  # hero + gallery thumbnails
            except (RuntimeError, OSError, ValueError):
                continue  # a single photo failing shouldn't sink the report
        lines += _property_block(o, embeds)
    report.write_text("\n".join(lines))
    return report
