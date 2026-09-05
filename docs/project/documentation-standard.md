# Documentation standard

**How every page in `docs/` is written: fourteen rules distilled from the page
that got it right — [reference/ragas.md](../reference/ragas.md) — which rule
applies to which folder, which rules the test suite enforces, and how to
bring an older page up to the standard.** This is the process page for the
documentation; what the documentation *covers* is the index,
[docs/README.md](../README.md). Adopted 2026-09-04.

## 1. Why a standard

Three facts about this repository make it worth writing the rules down:

- **The docs are the workshop handbook and the defence material.** A page
  that explains a mechanism without showing it, or quotes a number without
  saying where it came from, is a page someone will be caught out on.
- **The docs are tested.** Links, coverage of every setting and tool, and
  numbers that must agree across pages already fail the build
  ([handbook/09](../handbook/09-testing-operations.md) lists the tests). Rules
  that can be checked mechanically survive; the rest need a checklist that is
  actually read, which is this page.
- **Most pages are written by AI coding agents** working from
  [CLAUDE.md](../../CLAUDE.md). Without an explicit standard, every page
  converges on the writer's habits rather than the reader's needs.

The exemplar is [reference/ragas.md](../reference/ragas.md). Every rule below
names the section of that page that demonstrates it, so the rule and its
example cannot drift apart.

## 2. The rules

Each rule states what to do, why, where ragas.md shows it, and how it is
enforced — *tested* (fails the build, see §4) or *reviewed* (the checklist).

1. **Open with the scope in one bold paragraph.** List the questions the page
   answers, then one sentence on what it is *not*, with a link to the
   neighbouring page. A reader decides in ten seconds whether they are in the
   right place. — ragas.md, the paragraph under the title. *Tested.*

2. **Use one fixed skeleton for reference pages.** Numbered H2 sections in
   this order: what it is → how it works → where it lives → how to run it →
   how to see it → how to prove it → showing it live → reading it honestly →
   troubleshooting → related. A reader learns once where "how do I run it"
   is and finds it on every page. Omit a section the subject has nothing to
   say in; never fill one with filler, and never reorder. — ragas.md §1–§10.
   *Tested* (numbering, a troubleshooting table, Related last); order of the
   rest *reviewed*.

3. **Every mechanism gets a worked example from this project's own data.**
   Not an abstract formula: the fixture corpus, a real turn, a real file. If
   a mechanism cannot be shown on this project's data, the explanation is not
   finished. — ragas.md §2, the billing-service chunk scoring 1.00 and 0.33.
   *Reviewed.*

4. **Every number is measured, dated, and reproducible.** State the command
   that produced it, the artifact it lives in, and the commit; bold the
   number. Nothing quoted from literature or memory. — ragas.md §5, the
   history row with its date and SHA; "0.996 unrounded". *Tested* (the page
   carries a date); provenance *reviewed*.

5. **Every capture is real output, and every capture is read line by line.**
   Rendered from an actual run, never mocked up. Descriptive alt text, a
   filename prefixed by topic in `docs/images/`, and directly below it one
   bullet per line saying what the reader is looking at and why it matters.
   A screenshot without a reading is decoration. — ragas.md §5, "Line by
   line". *Tested* (alt text present, no unused image); the reading
   *reviewed*.

6. **"Where it lives" is a file-and-role table, then "what one run does, in
   order".** Every participating file gets a row with a relative link and its
   role in one sentence; the runtime sequence follows as a numbered list.
   This is what turns a concept page into a map of the code. — ragas.md §3.
   *Reviewed.*

7. **Commands run as pasted, from the repository root.** A comment per
   command saying what it is for, the PowerShell variant whenever an
   environment variable is involved, and a table of runtime and cost per
   variant. Nobody should guess whether a command takes a minute or six, or
   costs money. — ragas.md §4. *Tested* (every command names files that
   exist — the existing coverage test); the rest *reviewed*.

8. **Enumerations become tables; reasoning stays prose.** Flags, file roles,
   comparisons and symptom/cause/fix are tables; why something was decided
   is a paragraph. A table cell is never a bare word — it says what the thing
   does. — ragas.md §1, the metrics table and the paragraph after it.
   *Reviewed.*

9. **Decisions are written with the alternatives that lost.** A reader who
   asks "why not X" should find X already named and priced. — ragas.md §1,
   the "Used here" column with five *no*s and their reasons. *Reviewed.*

10. **Prove it, don't assert it.** Show the evidence that the capability
    works and, wherever possible, the negative case verbatim: what a failure
    looks like. A claim with no failure mode shown cannot be checked by the
    reader. — ragas.md §6, the control run and the printed gate message.
    *Reviewed.*

11. **Name the limitations before someone else does.** A "reading it
    honestly" section: known failure modes, what the thing does *not*
    measure or do, the sample-size caveat. This is the section a defence is
    judged on. — ragas.md §8. *Reviewed.*

12. **Troubleshooting rows quote the real message.** Only failures that were
    actually hit get a row; the symptom column carries the verbatim error;
    the fix is a command or a file, not "check your configuration". —
    ragas.md §9. *Tested* (the table's shape: Symptom … Fix); the verbatim
    messages *reviewed*.

13. **A demo script with spoken lines and a timing.** For anything
    demonstrable: the exact command, what to say while it runs (in italics),
    and how long it takes. The workshop is the audience of these pages. —
    ragas.md §7. *Reviewed.*

14. **End with "Related", each link with a reason.** Three to six links, each
    saying why the reader would go there next. Never a bare list of file
    names. — ragas.md §10. *Tested.*

## 3. Where the rules apply

| Folder | Rules | Why the difference |
|---|---|---|
| `reference/` | all fourteen | one subject end to end — the pages the standard was written from |
| `handbook/` | all except 2 (chapters keep their operator's-tour shape) | the ones handbook chapters tend to miss are 3, 4, 10 and 11 |
| `theory/` | 1, 3, 8, 9, 11, 14 | concepts are explained, not run: no captures, commands, demos or troubleshooting |
| `project/` | 1, 4, 8, 9, 14 | decisions and records; this page follows them itself |
| `docs/` root (`README.md`, `roadmap.md`) | 1, 4, 8, 9, 14 | the index and the reading path are records too: they are held to the `project/` rules |
| `qanda/` | exempt | its question-then-answer format is the point of the page |

Two mechanical conventions apply to every adopted page regardless of folder,
because the tests are simpler that way and readers cross-reference by "§3":
H2 sections are numbered contiguously (`## 1. …`), and the page carries the
date of its measurements. Rule 2's *fixed order* of sections is what
reference pages alone must follow.

## 4. How the rules are enforced

The mechanical half lives in
[tests/test_docs_standard.py](../../tests/test_docs_standard.py), next to
the existing link, coverage and consistency tests, one file per concern.

| Test | Rule | What fails |
|---|---|---|
| `test_sections_are_numbered_and_contiguous` | 2 | H2s not numbered `1.`, `2.`, … without gaps |
| `test_opens_with_a_bold_scope_paragraph` | 1 | the first paragraph under the title does not start bold |
| `test_ends_with_related_links_that_say_why` | 14 | the last section is not "Related", or a link has no reason after it |
| `test_troubleshooting_table_has_symptom_cause_fix` | 12 | a Troubleshooting section whose table is not Symptom … Fix |
| `test_measured_numbers_carry_a_date` | 4 | a page with no date on it at all |
| `test_every_image_has_alt_text_and_is_used` | 5 | an image without alt text, or a file in `docs/images/` no page uses |

**The ratchet.** The per-page tests run only on pages listed in `ADOPTED`
inside the test file. A page is added to the list when it has been brought
up to the standard, and it can never fall back — that is the whole
mechanism. The image test runs across every page, because an unlabeled or
unused image is wrong anywhere. The `ADOPTED` list in the test file is the
authoritative record of which pages are held to the standard; on
2026-09-05 it covered every page in `reference/`, `theory/`, `project/`
and `handbook/` — 36 pages, with `qanda/` exempt.

**The review checklist** — the eight rules no test can judge, to read before
committing a page: 3 worked example on our data · 6 file table + run
sequence · 7 commands commented, timed, priced · 8 tables for lists, prose
for reasons · 9 losing alternatives named · 10 the negative case shown ·
11 limitations named · 13 demo script with spoken lines.

## 5. Bringing an existing page up

1. Read the page against the fourteen rules and list what is missing; most
   pages fail 3, 4, 10 and 11 first.
2. Add what is missing in the skeleton's order. For rule 4, re-measure rather
   than re-quote — the numbers in older pages were the ones that drifted.
3. Render captures from real runs (the renderer pattern behind
   `docs/images/ragas-*.png` is a small script over Pillow: real text,
   monospace, dark background).
4. Add the page to `ADOPTED`, run `uv run pytest tests/test_docs_standard.py
   tests/test_docs_links.py tests/test_docs_coverage.py
   tests/test_docs_consistency.py -q`, fix what fails.
5. Commit the page and the ratchet change together.

The order the first pass took, furthest from the standard first: the older
reference pages ([tools.md](../reference/tools.md) and
[security.md](../reference/security.md) had no captures and no demo
script), then the theory chapters that quoted numbers without a date, then
the project records, then the handbook chapters. Reference pages were
rewritten by hand with measurements and captures from real runs; theory,
project and handbook pages were brought up by coding agents working from
this page, each agent's output audited with the snippet in §4 before the
page was adopted.

The captures were re-taken on 2026-09-05 from the current build: the UI
(empty state, a turn in Dev mode, the details timeline, the Documents
panel), a Jaeger trace with the cloud lenses on, the Grafana dashboard with
traffic, and Qdrant's collection view — all driven through a headless Edge
over the Chrome DevTools Protocol by a script kept outside the repository.
Re-capture when the header, the panels or the dashboards change.

## 6. The skeleton, ready to paste

```markdown
# <Subject> — <what it is in four words>

**What <it> is, how it works here, how to run it, how to read what it
prints, and how to prove it.** For <the neighbouring subject>, see
<the neighbouring page, linked>; this page is <the subject> end to end.

## 1. What <it> is
## 2. How <it> works            <!-- mechanism + a worked example on our data -->
## 3. Where it lives in this project   <!-- file/role table, then "what one run does, in order" -->
## 4. How to run it             <!-- commented commands, PowerShell variant, runtime/cost table -->
## 5. How to see it             <!-- real captures, each read line by line -->
## 6. Proving it                <!-- the evidence, and the failure case verbatim -->
## 7. Showing it live           <!-- command, spoken lines in italics, timing -->
## 8. Reading it honestly       <!-- limitations, what it does not do -->
## 9. Troubleshooting           <!-- | Symptom | Cause | Fix | with real messages -->
## 10. Related                  <!-- 3–6 links, each with a reason -->
```

## 7. What the rules cannot do

- **They check shape, not truth.** A dated number can still be wrong; a
  worked example can still be misleading. The consistency tests catch a few
  contradictions; a reader who re-runs the command catches the rest. Rule 4
  exists to make that re-run possible, not to replace it.
- **They invite ceremony.** A skeleton is easy to fill with empty sections.
  The rule is to omit, and a reviewer should treat a two-line section as a
  missing one.
- **They cost time.** A page to this standard is hours, not minutes, mostly
  spent running things to capture and measure. That is the price of pages
  that survive a hostile question; it is also why the ratchet exists rather
  than a flag day.

## 8. Related

- [reference/ragas.md](../reference/ragas.md) — the exemplar every rule points at
- [reference/logfire-langfuse.md](../reference/logfire-langfuse.md) — the second adopted page, written to the standard from the start
- [docs/README.md](../README.md) — the index: what the documentation covers, and where a new page is registered
- [handbook/09 — Testing & operations](../handbook/09-testing-operations.md) — the docs-as-tests suite this standard's tests join
- [CLAUDE.md](../../CLAUDE.md) — what coding agents read first; it points here
