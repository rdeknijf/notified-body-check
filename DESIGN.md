---
name: nando-api
description: A registry card file for EU notified body status, set in one carbon ink and one vermilion stamp ink on warm card stock.
colors:
  paper: "#efe9dc"
  card: "#fbf8f1"
  manila: "#e2cf9f"
  manila-edge: "#c9b27a"
  ink: "#1b2233"
  ink-2: "#454d5e"
  ink-3: "#5d6474"
  rule: "#d3c9b3"
  rule-strong: "#b5a98c"
  stamp: "#b8321c"
  stamp-deep: "#962613"
  on-stamp: "#fff8f0"
typography:
  display:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(2.1rem, 1.4rem + 3vw, 3.6rem)"
    fontWeight: 760
    lineHeight: 1.12
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "clamp(1.5rem, 1.2rem + 1.2vw, 2.1rem)"
    fontWeight: 720
    lineHeight: 1.12
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 720
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    letterSpacing: "0.08em"
  data:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.9375rem"
    fontWeight: 400
rounded:
  focus: "2px"
  control: "3px"
  stamp: "4px"
  card: "6px"
spacing:
  gutter: "clamp(1rem, 4vw, 3rem)"
  measure: "68ch"
  form-gap: "0.75rem"
  stack: "1.25rem"
  intro-stack: "1.5rem"
  section: "clamp(3rem, 7vw, 6rem)"
components:
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.55rem 1.1rem"
    height: "2.75rem"
  button-ghost-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
  button-primary:
    backgroundColor: "{colors.stamp}"
    textColor: "{colors.on-stamp}"
    rounded: "{rounded.control}"
    padding: "0.55rem 1.1rem"
    height: "2.75rem"
  button-primary-hover:
    backgroundColor: "{colors.stamp-deep}"
    textColor: "{colors.on-stamp}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.65rem"
    height: "2.75rem"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "1.4rem clamp(1rem, 3vw, 1.75rem) 1.25rem"
  card-tab:
    backgroundColor: "{colors.manila}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    rounded: "{rounded.stamp}"
    padding: "0 0.9rem"
    height: "1.45rem"
  stamp-active:
    backgroundColor: "transparent"
    textColor: "{colors.stamp}"
    rounded: "{rounded.stamp}"
    padding: "0.35rem 0.7rem"
  stamp-adverse:
    backgroundColor: "{colors.stamp}"
    textColor: "{colors.on-stamp}"
    rounded: "{rounded.stamp}"
    padding: "0.35rem 0.7rem"
  stamp-unknown:
    backgroundColor: "transparent"
    textColor: "{colors.stamp}"
    rounded: "{rounded.stamp}"
    padding: "0.35rem 0.7rem"
  curl-bar:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.card}"
    typography: "{typography.data}"
    rounded: "{rounded.control}"
    padding: "0.7rem 0.85rem"
---

<!-- markdownlint-disable-next-line MD025 -->
# Design System: nando-api

## Overview

**Creative North Star: "The Register Card File".**

The site is a registry card file. Each answer is an index card for one body: the four-digit body number sits on a manila tab, the facts sit in ruled entry rows, and the status lands as a rubber stamp. Everything else on the page (the endpoint ledger, the change log, the export list, the numbered terms) is a ruled register on the same warm card stock.

Two inks do all the work. A carbon blue-black writes the text and the entries, and one vermilion stamp ink marks what matters: the stamp, the primary action, the method verb in a call, the clause number, the link underline. Rank comes from weight, case, and rules before size. Depth is limited to the one card that holds the live answer; the rest of the page is flat paper divided by hairlines.

The system has one light theme only (`color-scheme: light`). There is no dark mode, and no surface may add one without a new decision. The wordmark renders the `SERVICE_NAME` constant from `src/nando_api/config.py`; no visual asset may hard-code the service name.

**Key Characteristics:**

- Warm card stock ground with a lighter card surface and a manila tab.
- One text ink in three strengths, one stamp ink plus its deep press state.
- A grotesk for prose and headings; a mono only for code, numbers, and data entries.
- Status shown as a stamped word in a bordered, rotated mark, never as a colour alone.
- Hairline rules and 2px heavy rules organise every list, table, and card.

## Colors

A warm, low-chroma paper palette with one saturated vermilion that no other hue competes with.

### Primary

- **Stamp Vermilion** (stamp): the only accent. It colours the status stamp, the primary button, the top rule of the card entries, the HTTP method in the endpoint ledger, the change kind in the log, the clause numbers in the terms, list markers, link underlines, the focus ring, the text caret, and the selection.
- **Pressed Vermilion** (stamp-deep): hover state of the primary button and the hover colour of links.
- **Stamp Paper** (on-stamp): text on a solid vermilion fill (primary button, adverse stamp, selection).

### Neutral

- **Card Stock** (paper): the page ground.
- **Index Card** (card): raised surfaces that hold data: the check card, inputs, the change log panel, the raw JSON block. Also the text colour on the dark curl bar.
- **Manila** (manila) and **Manila Edge** (manila-edge): the card tab fill and its border. Reserved for the tab and the favicon.
- **Carbon Ink** (ink): headings, body text, the newest log entry, button outlines, the heavy rules over tables and the terms list, and the fill of the curl bar.
- **Faded Ink** (ink-2): secondary prose, ledes, labels, table heads, older log entries.
- **Pencil** (ink-3): the faintest text: placeholders, the unofficial notice, derived-fact notes, input borders, the oldest log entries.
- **Ruled Line** (rule): hairlines between rows and entries.
- **Strong Rule** (rule-strong): section dividers, the masthead underline, card and panel borders, the scrollbar thumb.

### Color Rules

**The One Ink Rule.** Vermilion is the only accent hue. Never add a second accent; give a new mark weight, case, or a rule instead of a new colour.

**The Ink Age Rule.** In a dated list, the newest entry takes the darkest ink and older entries step down to faded ink, then pencil.

## Typography

**Display and Body Font:** Schibsted Grotesk (with system-ui, sans-serif), self-hosted variable 400 to 900
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, monospace), self-hosted 400 and 600

**Character:** A dense, slightly condensed newspaper grotesk carries the voice; the typewriter-plain mono carries the record. The pairing reads as a form filled in by hand on a printed card.

### Hierarchy

- **Display** (760, fluid 2.1rem to 3.6rem, 1.12): the one page title (landing claim, terms title at a smaller clamp of 2rem to 2.75rem).
- **Headline** (720, fluid 1.5rem to 2.1rem, 1.12): section titles.
- **Title** (720, 1.375rem, 1.2): the body name on the card; terms clause headings use the same weight at 1.125rem.
- **Body** (400, 1.0625rem, 1.55; 1rem under 40rem): prose, capped at the 68ch measure; ledes run at 1.1875rem in faded ink.
- **Label** (700, 0.75rem, 0.08em tracking, uppercase): form labels, entry names on the card, table heads, and small panel headings (0.8125rem). These label real fields and blocks; they never sit above a heading as decoration.
- **Data** (mono 400, 0.9375rem, fixed-width figures): body numbers, dates, entry values, calls, feed URLs, export rows, input text. Mono 600 marks the emphasised figure (tab number, counts in the facts line, the method verb).

### Type Rules

**The Two Voices Rule.** The grotesk speaks; the mono records. Use the mono only for code, numbers, identifiers, and data values, never for prose or headings.

**The Rank Before Size Rule.** Separate levels with weight, uppercase tracking, and rules before you reach for a larger size.

## Layout

The page is a stack of full-width bands divided by strong rules. Each band pads with a fluid section space and the side gutter, and its content sits in a centred container of 82rem. The first band is a two-column split (copy left, the live check right at a slightly wider 1.05 fraction); the alerts band and the source band use two-column grids too. Prose never runs wider than the 68ch measure, and ledes and the facts line stop at 36rem. The terms page is a single 46rem column.

Spacing has no numeric scale. It uses a small set of reused gaps: 0.75rem between form controls and buttons, 1.25rem between blocks in a section, 1.5rem in the intro stack, and the fluid gutter and section padding.

Responsive behavior has two steps. Under 60rem every two-column grid collapses to one column. Under 40rem the body text drops to 1rem, the check form puts its button on its own full row, the stamp moves under the card name, the card entries stack label over value, the ledger drops its header row and stacks each call over its answer, and the log stacks time over text.

## Elevation & Depth

The system is flat paper with tonal layering: paper ground, a lighter card surface, and the manila tab. Borders and rules carry structure. Exactly one surface casts a shadow: the check card, which lifts off the page as the physical index card.

### Shadow Vocabulary

- **Card lift** (`box-shadow: 0 1px 0 var(--rule), 0 12px 28px -18px rgba(27, 34, 51, 0.45)`): a hairline under the card plus a soft, tight ambient fall in carbon ink. Only on the check card.

### Depth Rules

**The One Card Rule.** Only the answer card is lifted. Panels, inputs, and code blocks stay flat with a strong-rule border.

## Shapes

Corners are nearly square. Controls (buttons, inputs, the curl bar) take a 3px radius, the stamp and the tab top take 4px, and the card takes a cut 2px top-left corner against 6px elsewhere, so the tab joins the card edge. Rules come in two weights: a 1px hairline between rows and a 2px heavy rule that opens a register (ink over the ledger and the terms list, vermilion over the card entries). The stamp is the one tilted form, rotated by minus 7 degrees.

## Components

### Buttons

Plain and firm, like a printed form control.

- **Shape:** slightly softened rectangle (control radius), 1.5px border, minimum height 2.75rem.
- **Primary:** vermilion fill and border with stamp-paper text, 650 weight at 0.9375rem. One primary per view: "Run a check" and the form's "Check".
- **Ghost:** transparent with a carbon border and carbon text; hover fills it with carbon and turns the text to card.
- **Hover / Focus:** background, text, and border colour transition over 160ms on the shared ease-out curve; focus shows the 2px vermilion outline at 3px offset. Disabled drops to 55% opacity with a progress cursor.

### Inputs / Fields

- **Style:** card fill, 1px pencil border, control radius, 2.75rem minimum height, mono text at 1rem (selects use the grotesk). Labels sit above in the label style.
- **Focus:** 2px vermilion outline at 1px offset; the caret is vermilion.
- **More options:** extra fields fold behind a small disclosure in faded ink, and open into a two-column grid.

### Navigation

- **Style:** a masthead row with the 800-weight wordmark on the left and section links in faded ink at 0.9375rem on the right, over a strong-rule underline. Hover darkens the link to carbon and adds a vermilion underline. On narrow screens the links wrap under the wordmark.

### Cards / Containers

- **Corner Style:** cut top-left corner, card radius elsewhere.
- **Background:** card.
- **Shadow Strategy:** card lift (see Elevation & Depth).
- **Border:** 1px strong rule.
- **Internal Padding:** about 1.4rem top, fluid 1rem to 1.75rem sides.
- **Tab:** a manila tab with a manila-edge border sits above the top-left edge and carries a small "NB" label in faded ink and the four-digit body number in mono 600.
- **Panels:** the change log and the raw JSON block use the card fill with a strong-rule border and no shadow.

### Stamp

The status mark on the card. Uppercase grotesk 800 at 1rem with 0.12em tracking, a 2.5px vermilion border, rotated minus 7 degrees, multiplied onto the card.

- **Active:** vermilion text with a double ring (an inset card line and an inset vermilion line), for a notified body.
- **Adverse:** solid vermilion fill with stamp-paper text, for suspended, withdrawn, expired, or not notified.
- **Unknown:** dashed vermilion border, no ring, when the status cannot be read.
- **Motion:** each new answer presses the stamp in over 420ms (from 1.14 scale with a slight blur to rest) on the ease-out curve. Reduced motion removes the press, the button transition, and smooth scroll.

### Entries

A two-column definition list under a 2px vermilion rule: uppercase labels in faded ink on the left, mono values on the right, a hairline under every row. A fact this service works out (not copied from the register) carries a small grotesk note in pencil after the value. A false value is struck through with a 2px vermilion line.

### Curl Bar

The copyable call. A carbon bar with card-coloured mono text at 0.8125rem that wraps anywhere, and a "Copy" button on the right behind a faded-ink divider. The button fills vermilion on hover and confirms with "Copied" for 1.6 seconds.

### Registers

Ruled lists that share one grammar: an uppercase label head or a heavy rule on top, hairlines between rows, mono for the data column.

- **Ledger:** the endpoint table; the method verb is mono 600 in vermilion before the path.
- **Log:** dated change entries, mono time in a 6.5rem column, change kind in uppercase vermilion, ink ageing down the list.
- **Export list:** mono rows of date and file links.
- **Terms list:** numbered clauses under a heavy carbon rule, with the number in mono 600 vermilion in a 2.25rem column.

## Do's and Don'ts

### Do

- **Do** show every status as a stamped word; the tone (active, adverse, unknown) changes the form of the mark, not only its colour.
- **Do** keep vermilion as the one accent and use it for marks, rules, and the primary action.
- **Do** set code, numbers, identifiers, and data values in IBM Plex Mono, and everything else in Schibsted Grotesk.
- **Do** mark facts this service derives with a small pencil note, separate from facts copied from the register.
- **Do** separate rows with the hairline rule and open a register with the 2px heavy rule.
- **Do** keep the light theme only and declare `color-scheme: light`.

### Don't

- **Don't** add a second accent hue or a status colour scale (no green for active, no amber for warnings).
- **Don't** lift any surface other than the answer card with a shadow.
- **Don't** set prose or headings in the mono.
- **Don't** add a dark theme without a new decision.
- **Don't** hard-code the service name in markup or assets; render `SERVICE_NAME`.
