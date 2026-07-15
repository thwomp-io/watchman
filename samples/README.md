# Sample weight packs

A **weight pack** is a portable, self-contained bundle of the *weights* (scoring rules, preferences,
thresholds) and *data/state* (holdings, trips, watchlists) that the harness loads to drive a scenario.
You maintain your own packs for the topics you care about; the apps — including the Watchman console —
are *consumers* of whatever pack is loaded. **Swap the loaded pack to re-render the console against a
different scenario.**

These bundled samples are **fully fictional** and exist so a fresh clone runs **out-of-the-box** with no
configuration, and so you can see the load/unload-a-scenario flow before building your own.

## Layout

```
samples/packs/
  <pack-name>/
    pack.yaml # manifest: name, title, description, lanes, default
    finance/ # per-lane data; present only for the lanes this pack provides
      portfolio.yaml
    travel/ # (e.g.) weights.yaml, preferences.md, destinations/…
    career/ # (e.g.) watchlist.yml, …
```

- **`pack.yaml`** declares the pack's identity, the `lanes` it provides, and whether it's the `default`.
- **Per-lane subdirs** hold that lane's weights + data, mirroring the structure each lane reads. A pack
  only needs subdirs for the lanes it covers.

## Bundled samples

Each bundled pack is a **complete persona** — it ships data for *every* lane (finance + career + travel),
so loading one swaps the **whole console**. (A pack that omitted a lane would let that lane's dashboards
fall back to your real corpus — so personas are always complete.)

| Persona | What it is (across all three lanes) |
|---|---|
| `demo-investor` *(default)* | An established ~$1M household — a diversified index-anchored portfolio + a few single-stock convictions (finance) · a light/passive senior-role watch (career) · a premium trip pipeline from a Minneapolis home base (travel). |
| `demo-growth` | An early-career infrastructure engineer — an aggressive ~$250k growth-tilted book (finance) · an active platform/SRE job search across public infra companies (career) · a budget trip pipeline from a Denver home base (travel). |
| `college-grad` | A new graduate just starting out — a small first-brokerage book (finance) · an entry-level new-grad search (career) · a couch-surfing budget trip pipeline from an Austin home base (travel). |
| `early-retiree` | A conservative FIRE household — a large dividend/index-tilted book drawing down (finance) · a passive/optional watch (career) · a leisurely trip pipeline from a Scottsdale home base (travel). |

Load one, then swap to the other, to watch the whole console re-render (the `--pack` flag sits at the
natural end of any lane command):

```sh
hn finance networth --pack samples/packs/demo-investor # the ~$1M household
hn finance networth --pack samples/packs/demo-growth # the aggressive early-career book (the swap)
hn career shortlist --pack samples/packs/demo-growth # the active role search (offline)
hn travel trips --pack samples/packs/demo-investor # the premium trip horizon (keyless)
```

> Each lane ships sample data; any private lane data is never published. Build your own persona to
> replace these — that's the real point (see the [`corpus-operator` skill](../skills/corpus-operator/SKILL.md)).

## Using a pack

A pack is loaded by pointing the harness at it (the loader wires the pack root in front of the existing
per-lane data paths). Your own real data lives **outside** the public repo — in your private corpus,
config-pointed — so nothing personal is ever committed here.

> Building your own pack from scratch is the real point of the project: the harness ships an agent skill
> that interviews you and maintains your pack over time. The samples are the warm start.
