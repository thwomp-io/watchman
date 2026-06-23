# viz/ — D3 → static-SVG renderer (quarantined Node subproject)

The visualization layer for the toolkit. **Quarantined JS toolchain**: a self-contained Node
project the Python side shells out to. Python never imports JS; it calls `node render.js` and reads
the SVG back. Keeps the JS deps out of the Python (uv) toolchain.

## Bootstrap (one-time)

```sh
cd viz && npm install # installs d3 + jsdom (node_modules is gitignored)
```
Requires **Node on PATH** (`brew install node`). The Python bridge (`src/harness/viz.py`)
errors clearly if Node or the renderer is missing.

## How it works

```
node render.js <type> <data.json> <out.svg>
```
- Builds an SVG **server-side** using D3 (`d3-selection`/`scale`/`time`/`shape`) against a jsdom DOM,
  then serializes `<svg>.outerHTML` to a standalone `.svg` file.
- **Static SVG by design** — renders as a standalone SVG, version-controlled and diffable. NOT
  interactive HTML. If interactivity is ever wanted, that's a separate artifact — revisit deliberately.
- Normally invoked via the Python `hn travel viz` CLI / `make_diagram` MCP tool (which preps the data +
  writes into `{doc}/visuals/`), not by hand.

## Diagram types (the `RENDERERS` map)

- **`timeline`** — date-window day-grid; weekend shading; per-day event chips by lane; legend.
- **`schedule`** — compact day-planner: day columns × time-of-day axis; hour gridlines; **data-driven
  availability bands** (`d.availability`; default = a sample weekday/weekend availability pattern); time-positioned
  item blocks; optional ambient **`markers` rail** above the grid (awareness, not blocks).
- **`schedule-bank`** — `schedule` **plus an options-bank panel** below the grid (`d.bank`), grouped +
  color-dotted by lane to match the grid. Shares the `paintGrid` painter with `schedule` (so they never
  drift); `schedule` calls `paintGrid` only, `schedule-bank` calls `paintGrid` + `paintBank`.
- **`radial`** — drive-time map: home at center; sqrt-spaced rings = travel time; options by bearing;
  indoor/outdoor coloring.
- **`compare`** — radar/spider: candidates × qualitative axes as overlaid translucent polygons (the
  diverge-pool decision view). Data: `{axes, candidates:[{label, values}], max?}`. Reuses `makeSvg`.

### Adding a type
1. Write `function renderMyType(root, d) { … }` (append a `<svg>` to `root`; reuse `parseDay`,
   `isWeekend`, `truncate`, `fmtClock`).
2. Register it in `RENDERERS` (top of `render.js`).
3. Add the string to `KNOWN_TYPES` in `src/harness/viz.py`.
4. **Always eyeball** the output: `qlmanage -t -s 1600 -o /tmp out.svg` → inspect the PNG. Structural
   validation (element counts) is NOT enough — a `d3.timeDay.range` start-ceil off-by-one once shifted
   every date by +1 day and *only the visual* caught it. Floor day-ranges to local midnight.

## Gotchas
- `parseDay` uses **noon-local** to avoid TZ rollover; `d3.timeDay.range` **ceils** its start to the
  next midnight, so floor first (`d3.timeDay.floor`) or you silently drop day 0.
- Top-level `render()` runs before `const` helpers defined below it would initialize — keep shared
  helpers as **hoisted `function` declarations**, not `const` arrows.
