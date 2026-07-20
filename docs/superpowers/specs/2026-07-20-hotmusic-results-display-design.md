# Hotmusic results display fixes

Date: 2026-07-20
Scope: `frontend/src/lib/components/results/HotmusicResults.svelte`, `frontend/src/lib/components/MutationHeatmap.svelte`

## Background

Four display issues in the hotmusic results page (`HotmusicResults.svelte`), reported by the user after comparing the frontend output against the reference report the hotmusic tool itself generates (`frontend/static/examples/hotmusic/query_47691_graphic.pdf`).

The `.hots` file (parsed by `frontend/src/lib/utils/hotmusic.ts` into `HotSummaryRow`) defines, per position: `avgDtm` (average ΔTm across all mutations tested at that position), `sumNegDtm` (sum of negative ΔTm values, always ≤ 0), `sumPosDtm` (sum of positive ΔTm values, always ≥ 0). File header: "ΔTm>0 means stabilizing mutation".

## 1. Mutation sensitivity profile chart — use sum instead of average

**Current:** `makeProfileChart()` plots `r.avgDtm` per position. Since most individual mutations at a position are destabilizing, `avgDtm` is usually negative, so most bars point down.

**Change:** Plot `r.sumPosDtm` instead. This is exactly the quantity the hotmusic tool's own PDF report plots, titled "Sequence optimality (sum of positive dT_m per sequence position)" — confirmed by cross-checking the PDF's per-residue numeric labels against the `.hots` file's `sumPosDtm` column (exact match, e.g. residue A5 = 1.19 in both).

Since `sumPosDtm ≥ 0` always, bars naturally point upward — this is what "peaks direction inverted" resolves to; no separate sign-flip logic is needed beyond switching the data source.

**Also update:** Y-axis title and modal subtitle text from "Average ΔTm (K)" / "Average ΔTm per position — peaks highlight thermosensitive hotspots" to reflect "Sum of positive ΔTm (K)" / "Sum of positive ΔTm per position — peaks highlight positions tolerant to stabilizing mutations".

Bar color still follows the (now-inverted, see §2) threshold scale, keyed on the plotted value's sign.

## 2. Invert ΔTm colors everywhere — negative = green, positive = red

Applies to every ΔTm color site in scope, using the raw ΔTm value's sign (not the tool's "stabilizing/destabilizing" semantics):

- `dtmHex(v)` (3D viewer residue color) and `dtmColorChart(v, alpha)` (profile chart bars) — swap the 4-tier thresholds so `v > 0.5` → red-strong, `v > 0` → red-light, `v > -0.5` → green-light, else → green-strong (mirror of current green/red assignment).
- `dtmClass(v)` and its CSS (`.dtm-ss`/`.dtm-sl`/`.dtm-dl`/`.dtm-ds`) — same swap, light/dark theme variants both updated.
- `.green-val`/`.red-val` on the `sumNegDtm`/`sumPosDtm` table columns — swap so `sumNegDtm` (≤0) is green, `sumPosDtm` (≥0) is red.
- `.bar-pos`/`.bar-neg` per-mutation detail bars — swap.
- Heatmap (`MutationHeatmap.svelte` + hotmusic's wrapper): replace `hmDtmColor` (currently `ddgColor(-v, ...)`, borrowing popmusic's blue/red diverging scale) with a new hotmusic-local green/red diverging color function applied to the raw (non-negated) value. Popmusic's own heatmap (`ddgColor` used directly) is untouched.
- Legend copy near the heatmap colorbar updates from "blue = stabilizing (ΔTm > 0), red = destabilizing (ΔTm < 0)" to "green = ΔTm < 0, red = ΔTm > 0".

## 3. Secondary-structure colors and labels, matching the reference PDF

Colors extracted by rendering the PDF to an image and sampling fill colors at known per-residue grid positions, cross-verified against the `.hots` file's SS codes (not eyeballed):

| Code | Label | Hex |
|---|---|---|
| H | Helix | `#D90000` |
| G | 3₁₀-Helix | `#FF0000` |
| I | π-Helix | `#B30000` (not present in the sample protein; chosen as a distinct red-family shade consistent with H/G) |
| E | Sheet | `#8888FF` |
| B | Bridge | `#88FFFF` |
| T | Turn | `#FF8888` |
| S | Bend | `#12C83D` |
| C | Coil | `#88FF88` |

Replaces hotmusic's current `SS_COLORS` (purple/amber/cyan/slate scheme). `SS_LABELS` values change to include the one-letter code, e.g. `Bend (S)` instead of `Bend` — used by the structure filter pills, table SS badges, and scatter-chart legend/dataset labels. Popmusic's separate `SS_COLORS`/`SS_LABELS` constants are untouched.

## 4. 3D viewer default filter — show all stabilizing residues by default

**Current:** the Avg ΔTm range filter (`_dtmMin`/`_dtmMax`) defaults to `[rawMin, rawMax]` — every residue shown.

**Change:** default to `[0, rawMax]`, so on first load the 3D viewer shows every residue with `avgDtm > 0` (stabilizing, per the `.hots` file's own definition), colored via the inverted `dtmHex` scale. The existing filter inputs still let users widen the range manually.

## Out of scope

- Backend changes (`.hots`/`.hot` parsing is unaffected — only how the frontend uses existing fields).
- Popmusic's equivalent charts/heatmap/colors — this is hotmusic-only.
- Any new stabilizing/destabilizing toggle UI beyond adjusting the existing default filter values.
