# Detail blade: one row in full (S1b)

Status: draft for implementation, 2026-10-12. Owner: spec writer S1b.
The detail blade is blade 3. It shows one row of the turn blade in full: a model call, a tool or command, an agent run, a gap, or the turn's message (D33 "Detail blade for a model call, tool, agent or message"). It opens as the newest blade of the strip, taking the rest of the stage, while the turn blade stays readable at 400 px to its left and the session moves off-screen (SES-2), and it follows the turn's cursor (R3 §3; CONSOLIDATION-NOTES "j/k in the flow moves the cursor, and the detail blade follows").

Shared rules are in `session.md` (`SES-<n>`: navigation, cost rule, wall states, gap kinds) and `turn.md` (`TRN-<n>`: rows, failure rule, zoom). System rules are `SYS-<n>`. Requirement IDs here are `DET-<n>`.

Sources, in priority order: decisions D29, D31, D33; `CONSOLIDATION-NOTES.md`; `docs/specs/data-model.md` (**DM-<n>**); `REVIEW-v4-steps-r3.md` §3 (**R3**: gap detail) and `spike-steps-r3/app.js` `detailBlade()` (**R3-proto**); `REVIEW-v4-turn-r2.md` §5 (**TURN-R**: the detail per kind); `REVIEW-v4-detail.md` (**DET-R**: header, wells, disclosure, data problems); `evidence-turn-data.md` (**EVID**: what text exists where). All under `prototype notes (not in this repo)/` except the decisions and S2.

Facts only (D29): the blade states what the data says. It never explains why.

## 1. Frame and header

DET-1 — Frame: the rest of the stage, min 640 px (SES-2), l1, `--radius-pane`; it takes focus when it opens (SES-5). Padding 24 px; 32 px between sections; 8 px from a section head to its content. Sections have no borders; the only inset surfaces are the l2 wells (DET-R §2.11).

DET-2 — Meta line (14 px ink3, 8 px gaps; R3-proto `detailBlade`; DET-R §2.1): the turn's kind glyph + "Turn N" as a link (ink3, struct on hover and focus; clicking closes the detail and focuses the turn); "inside <agent name>" when the row is inside an agent; the clock (`HH:MM:SS` for a point, `HH:MM–HH:MM` for a gap, mono); spacer; the stepper: "R of M" (mono ink3; the row's position in the turn's current list, TRN-12, so it respects order, zoom and search), `chevron-left` "Previous (k)", `chevron-right` "Next (j)", `x` "Close (Esc)", each 28×28.

DET-3 — Title: a 20 px kind glyph (ink2; the row's glyph, TRN-31–34) + 6 px + the row's name (TRN-31–34) at 20/600 ink1, at most 2 lines, ellipsis, full text in `title` (DET-R §2.1).

DET-4 — Stepping. `j`/`k` (in the turn or this blade) and the stepper move the turn's cursor; the detail re-renders for the new row and keeps its scroll at the top. Section disclosure states (Show all, Raw input) reset per row. Crossing into another turn follows TRN-50.

DET-5 — Links to other rows (tool calls of a model call, calls of an agent, rows before and after a gap, agents running during a gap) are 32 px rows (`.ctx`): a leading figure or clock (mono), the row's glyph, its name (ink1, ellipsis), an optional trailing figure. Clicking one moves the turn's cursor to that row (SES-50, opening an agent band when needed); the detail follows.

## 2. Model call

DET-10 — Hero (TURN-R §5; R3-proto): the call's cost as the display figure (30/500 mono money), then "N% of the turn's cost" (ink3; N per SYS-29 percent rules; omitted when the turn cost is 0). Unpriced: DET-62.

DET-11 — Strip (one 14 px line, 16 px gaps): the full P4 model mark (SYS-41); `timer` model time + "model time"; "N tok/s out" when output tokens and model time are both > 0 (DM-47); "first token Ns" only when `ttft_ms` exists (DM-37, omp); `coins` tokens; "stop <stop_reason>" (ink3) when present.

DET-12 — Token line (14 px ink3, figures mono ink2; TURN-R §5): "in N · cache read N · cache write N · out N", plus "reasoning N" when `reasoning_tok` exists (Codex). A null figure is left out of the line.

DET-13 — Sections, in the call's content order (R3-proto; TURN-R §5):
- **Thinking** (`brain`, 14/600): one well per thinking block, prose (SYS-19), clamped to 3 lines with "Show all · N lines" (struct, `chevron-right` rotating). States: DET-63 (Codex encrypted), DET-64 (empty or redacted);
- **Said** (`message-square`) or **Final answer** (`message-square` + the "final" tag): one well per text block, clamped to 6 lines with "Show all · N lines";
- **Tool calls** (`terminal`, count in ink3): one link row (DET-5) per tool row of the call: duration, glyph, name; agent rows add their cost (money).
All text loads lazily (DET-60).

DET-14 — Speed note: when the call's time basis is `derived`, one `info` line (ink3) under the strip: "Model time is taken from log record times, so speed is approximate." (DM-37).

## 3. Tool or command

DET-20 — Hero (DET-R §2.2; R3-proto): the duration as the display figure (30/500 mono **ink1**; duration is not money), prefixed by `≈` when `time_prov = derived`, tooltip "End taken from the next record" (DM-40). Then, 16 px apart:
- status (DET-R §5.4; order as QRY-37, D38 L8): `exit_code` first, then `interrupted`, then the step's `outcome`, else none. Failed (TRN-32): `circle-alert` + "exit N" / "error" / "interrupted" in `--color-error`. Exit 0: "exit 0" in ink3, no glyph. No known status: nothing is rendered (never assume ok);
- the tool name (ink3);
- the output size from `result_bytes` ("11 KB output", ink3) when > 0.
A tool with no duration shows `—` as the hero (SYS-31 "not in data" applies to the panel's `info` line, DET-66).

DET-21 — Time flags (DM-40), each one `info` line (ink3) under the hero:
- `max_silence_ms ≥ 300,000`: "No records for <duration> inside this call. Its duration includes that time.";
- `waits_on_user = 1`: "This tool waits for your answer. Its duration includes your reply."

DET-22 — Skill load (SES-55): one line under the hero, `book-open` + "**<skill>** loaded here." (ink2).

DET-23 — **Command** (shell tools; `terminal`, 14/600): the full command in an l2 code well (mono 14 ink1, `pre-wrap`; long `&&` chains wrap), clamped to 6 lines with "All N lines"; a `copy` → `check` (1.2 s) button, 28×28, top-right inside the well (DET-R §2.6). The shown command is the full one from the source (not the 100-character label, not the description; DET-R §5.2).
**Input** (other tools; the tool glyph, "Input"): the main target (file path, pattern, URL) in the same well with copy; under it a collapsed "Raw input" (`chevron-right`) that shows the call's input JSON, pretty-printed, in a code well.

DET-24 — **Output** (no glyph, DET-R §4; head 14/600 "Output" + "11 KB · 214 lines" in ink3): in an l2 mono well, the first 20 lines, a clickable divider "… N more lines" (struct) that expands the rest in place, then the last 5 lines. When the output has 25 lines or fewer, all lines show. Empty output: "Empty" (ink3). Lazy (DET-60).

DET-25 — A spawn or SendMessage tool call is never shown as a tool detail; its row is an agent row and opens the agent detail (TRN-33; DET-R §2.9).

## 4. Agent

DET-30 — Hero (TURN-R §5; R3-proto): the agent row's cost (the whole engagement cost, SES-12) as the display figure (money), then "N% of the turn's cost" (ink3). In a zoom, the figure is the in-range part and the caption reads "N% of the range's cost" (TRN-24).

DET-31 — Strip: the agent's models as P4 marks; `timer` run time + "ran" (DM-36: engagement `wall_ms`; runs are counted per causing call, not across the agent's engagements (D41 M12), "N runs"); `arrow-left-right` model calls; `terminal` tool calls; `circle-alert` "N errors" in `--color-error` only when > 0.

DET-32 — **Asked** (`message-square-text`, 14/600; DET-R §2.3): the spawn prompt (for a SendMessage row: the message, headed **Message** with `send`) in an l2 prose well, clamped to 3 lines with "Show all · N lines". Lazy (DET-60): the text is read through the source pointer of the engagement's `caused_by` tool call (DET-R §5.5; S2 §5).

DET-33 — The rows button (R3-proto `openag`): a struct text button "Show its N rows in the turn" with an `l` key cap, or "Fold its rows" with `h` when they are shown (TRN-35). (Key caps here follow the SES-31 exception.)

DET-34 — **Calls by cost** (`arrow-left-right`, count in ink3; TURN-R §5): the agent's 5 costliest model calls as link rows (DET-5: cost in money, glyph, name), then one ink3 line "N other calls · $X" when there are more. The 5 rows plus $X equal the agent's cost to the cent. Clicking a call shows the agent's rows (TRN-35) and moves the cursor to it.
  Scenario: Given the 78-turn session, turn 35, auth-readiness ($3.28), then the hero reads $3.28 with "13% of the turn's cost" (R3 §7), and its top-5 calls plus "N other calls" sum to $3.28.

## 5. Gap

DET-40 — Hero (R3 §3; R3-proto): the duration as the display figure (mono ink1), then "N% of the turn's wall time" when the gap lies inside the turn's span, or "N% of the session's wall time" when it lies between turns or before the first message (SES-21, SES-36).

DET-41 — Reason line (ink2): a 10 px swatch in the state's colour and fill (SES-20), then "<state label>: <reason>." with the reason of SES-36 / TRN-34. For "Usage limit (inferred)" a second ink3 line states the inference: "Inferred: the next turn starts with “<first 60 characters of its text>”." (SES-17).

DET-42 — **Before** (head, no glyph): the row before the gap in As-it-ran order (DET-5), or nothing at the turn's start. **After**: the next main-thread row; when it starts another turn, an ink3 line "Starts turn N: <turn text, 120 characters>"; at the session's end, "Session end" (ink3).

DET-43 — **Agents running during it** (`bot`, count in ink3): up to 8 engagements whose run overlaps the gap, as link rows: their clock range (mono), `bot`, name, cost (money). Clicking one goes to its agent row (SES-50). Shown only when the count is > 0.

## 6. Message

DET-50 — The turn's opening message (TRN-3, TRN-5; TURN-R §5 "Message: the full ask from the source file"):
- meta line as DET-2 without the stepper (a message is not a row);
- title: the kind glyph + the kind label (SES-34 table);
- for an agent's message: "from <sender>" (ink3) under the title;
- for a slash command: the command and its arguments in a mono well;
- **Message** well: the full text from the source, prose (76 ch measure), not clamped, with the character count in the head (ink3) and a copy button. Lazy (DET-60).
`j`/`k` while the message detail is open move the turn's cursor and switch the detail to that row.

## 7. Text loading and data states

DET-60 — Lazy text (EVID "Full text is not stored"; DET-R §5.3; S2 §5). Full thinking, said text, commands, tool input and output, agent asks and messages are not in the store; the blade fetches each through the row's source pointer (`src://…`) when the section first renders (S3 owns the endpoint). The section head, counts known from the store (lines unknown until loaded; bytes from `result_bytes`) and the stored snippet render at once; the well shows 3 skeleton lines (l3 on the l2 well, SYS-54) until the text arrives. Text already fetched is not fetched again while the session stays open.
  Data: source text by pointer and part (input, thinking, text, output), with offset and byte limit (exists: `GET /api/source`; S3 confirms).

DET-61 — Unknown model (S2 §1 Codex has calls with an empty model; SYS-41): the mark is the small ● "other" with the name "unknown model" (ink3) and the tooltip "Model not recorded".

DET-62 — Unpriced call (cost null; design §4.7; SYS-32): the hero shows "No price" (20/600 ink3) in place of the figure, and one `info` line: "No price for <model id>. This call is not in the cost totals." Rows show a blank Cost; aggregates carry `+` (SYS-32).

DET-63 — Codex reasoning (EVID "Codex: reasoning is encrypted"): the Thinking well shows "Reasoning is encrypted in the Codex log." (ink3) and the reasoning token count; when a plaintext summary exists, it shows under the head **Reasoning summary**, clamped like thinking.

DET-64 — Empty or redacted thinking (EVID: 5.4k empty, 37 redacted Claude blocks): "No thinking text in the source." or "Thinking redacted in the source." (ink3) in place of the well.

DET-65 — Text too large. A section's first fetch asks for at most 256 KB (Open Q1). When the text is larger, the well shows what the head/tail rule of its section allows from the fetched part (output: first 20 lines; others: the clamp), then one ink3 line "Text too large to show in full: <size>" with the `src://` pointer in mono ink3 and a copy button. Output fetches the last 5 lines separately (offset near the end) so the tail is real.

DET-66 — Source missing (the file moved or was deleted; DET-R §2.7): one ink3 line with `info`: "Text not available: source file not found", then the `src://` pointer in mono ink3 with a copy button. Other sections keep working. A failed fetch for another reason shows the SYS-56 card in that section with Retry.

DET-67 — Images in text (EVID: Claude inline base64; Codex `input_image`; omp blob refs): never rendered inline; each shows as one ink3 line `info` "Image not shown (<size>)".

DET-68 — Derived and missing durations: a derived tool duration carries `≈` (DET-20); a model time is shown as stored, with the basis note of DET-14; a missing duration is `—` in the hero and blank in rows. Agent run time always comes from engagement times (DM-36), never from a first-to-last span.

DET-69 — Not in data, once per panel (SYS-31; DET-R §2.10): when a section has no source at all (for example no `src` pointer), the section is not rendered; if this removes the only content section, one `info` line says "No text recorded for this <row kind>."

DET-70 — Loading the blade: the header and hero render from the row data the turn blade already holds; only text wells load (DET-60). The blade itself never shows a full-blade skeleton. The sections that come from the op-detail fetch (`GET /api/ops/{op}/detail`: DET-13 sections, DET-23 command, DET-32 ask source, DET-34 call lists) show skeleton rows inside the section while the request is in flight, and the SYS-56 error card (title "Couldn't load details", with Retry) in place of the section if it fails (D38 M8). The header and hero stay.

## 8. Data the detail blade needs (summary for S2/S3)

| Need | Used by | Status |
|---|---|---|
| Per model call: in, cache read, cache write, out, reasoning tokens, `stop_reason`, `ttft_ms`, `time_prov`, model, cost; its steps with `src` | DET-10–14 | exists (design §4); shape needs S3 |
| Per tool call: `exit_code`, `is_error`, `interrupted`, step `outcome`, `result_bytes`, `time_prov`, `max_silence_ms`, `waits_on_user`, `skill_load`, `src` | DET-20–24 | S2 DM-30, DM-40; shape needs S3 |
| Per engagement: run time (union across engagements), call and tool counts, errors, models, `caused_by` `src` for the ask, top 5 calls by cost + rest | DET-30–34 | S2 DM-36; needs S3 |
| Gap: neighbours, next turn text, engagements overlapping it with cost | DET-40–43 | needs S3 |
| Source text by pointer, part, offset, max bytes; total size | DET-60, DET-65 | exists (`/api/source`); S3 confirms |
| Codex reasoning summary text | DET-63 | source only; S3 confirms the part name |

## 9. Resolved questions

| # | Question | Ruling | Applied in |
|---|---|---|---|
| R1 | Inline expansion or a blade? | A blade to the right, in every view (CONSOLIDATION-NOTES; D33) | DET-1 |
| R2 | Exit status when the store has none | Show nothing; never assume ok (DET-R §5.4) | DET-20 |
| R3 | Derived durations | `≈` with a tooltip (DM-40) | DET-20, DET-68 |
| Q1 | Size limit for a text fetch. | 256 KB per section on first fetch; "Text too large" beyond it (DET-65). | D37 |
| Q2 | DET-R proposed "Around it" (±2 neighbouring steps) and "Where it ran in the turn" strips; R3 did not keep them. | Not in v1: `j`/`k` with the detail following gives the neighbours. | D37 |
| Q3 | The agent detail could open the agent as its own blade with all its calls (TURN-R §8 "Agent blade"). | Not in v1: `l` shows its rows in the turn. | D37 |

## 10. Open questions (with the answer this spec applies)

None. Every question that was open here is resolved in section 9 by D37.
