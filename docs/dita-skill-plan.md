# DITA Authoring Skill — Development Plan

**Saved:** 2026-02-26
**Updated:** 2026-02-26 (expert round 2: Key Definition Maps, Schematron validation, structural triggers, DITA-OT 4.x project files)
**Author:** Claude (planning session)

---

## 1. Skill Overview

A Claude Code skill called **`dita-authoring`** that enables Claude to:

- Write syntactically correct, DITA-OT-parseable DITA XML (concepts, tasks, references, maps) following **DITA 2.0-Ready 1.3** patterns
- Provide beginner-friendly guidance with progressive depth — basics always available, advanced patterns in reference files
- Scaffold new content projects with reusable topic templates and map templates
- Design content architecture: topic hierarchies, reuse strategy, key management, metadata
- Review and fix existing DITA content — structural problems, broken references, 2.0-incompatible patterns
- Advise on content strategy: topic granularity, filtering, single-sourcing, localization prep

**Out of scope:** DITA-OT build pipeline automation (CI/CD, Ant/Gradle config, plugin development).

**Plugin location:** `~/projects/dita-authoring-plugin/`

---

## 2. Version Strategy: "DITA 2.0-Ready 1.3"

**Decision:** Write **valid DITA 1.3** that follows **DITA 2.0 structural preferences** to eliminate known breaking changes before they occur.

**Rationale:**
- Pure 1.3 builds in migration debt when 2.0 tooling matures
- Pure 2.0 breaks older DITA-OT plugins not yet updated
- "Forward-Ready 1.3" compiles on current toolchains, avoids known 2.0 incompatibilities

### Four Forward-Compatibility Rules

Encoded in SKILL.md constraint and `references/compatibility-2.0.md` with full before/after XML examples.

#### Rule A — Element-First (no redundant attributes)

| Context | Avoid | Use instead |
|---|---|---|
| Image alt text | `<image alt="..."/>` | `<image><alt>...</alt></image>` |
| Topicref title | `navtitle="..."` attribute | `<topicmeta><navtitle>...</navtitle></topicmeta>` |
| Topic summary | `<abstract>` (unless block-level needed) | `<shortdesc>` |
| Variable text | `<keyword>` in `<keydef>` | `<keytext>` (DITA 2.0); flag when used in 1.3 context |

#### Rule B — Simple Step Architecture

- **Avoid:** `<substeps><substep>…</substep></substeps>`
- **Use:** Nested `<steps>` or `<steps-unordered>` block inside the parent `<step>`
- **Troubleshooting:** Always a dedicated `<troubleshooting>` topic — never embedded sections in a `<task>`

#### Rule C — Modern Chunking Only

- **Avoid:** `chunk="to-content"`, `chunk="select-topic"`, `chunk="by-topic"`
- **Use only:** `chunk="split"` or `chunk="combine"`

#### Rule D — Multimedia

- **Avoid:** `<object>` for audio/video
- **Use:** `<video>` and `<audio>` (lwdita plugin for DITA-OT 3.x/4.x; native in DITA 2.0)

### Skill Constraint (in SKILL.md Purpose)

> All DITA XML generated must be **valid DITA 1.3** and follow **DITA 2.0 structural preferences** — elements over attributes, nested steps over substeps, `split`/`combine` chunking only — to ensure long-term maintainability.

---

## 3. Skill Location and Directory Structure

**Plugin directory:** `~/projects/dita-authoring-plugin/`

```
dita-authoring-plugin/
├── .claude-plugin/
│   └── plugin.json
├── README.md
└── skills/
    └── dita-authoring/
        ├── SKILL.md                      (~1,800 words, lean, imperative)
        ├── references/
        │   ├── topic-types.md            (concept, task, reference, troubleshooting, glossary)
        │   ├── maps-and-bookmaps.md      (hierarchy, reltables, chunking rules)
        │   ├── content-reuse.md          (conref, conkeyref, keyref, Key Definition Maps, push/pull)
        │   ├── metadata-filtering.md     (ditaval, profiling attributes, conditions)
        │   ├── dita-ot-reference.md      (RENAMED: pipeline + 4.x project files + common errors)
        │   ├── content-strategy.md       (topic sizing, modular authoring, single-source)
        │   ├── vscode-dita-setup.md      (VS Code extensions, schema validation, snippets)
        │   ├── compatibility-2.0.md      (full forward-compat checklist + before/after XML)
        │   └── common-elements.md        (curated quick-ref for ~35 most-used elements)
        ├── examples/
        │   ├── concept-complete.dita         (fully authored concept, 2.0-ready)
        │   ├── task-complete.dita            (nested steps, no substeps; uses keyref)
        │   ├── reference-complete.dita       (properties table, simpletable)
        │   ├── troubleshooting-complete.dita (standalone type)
        │   ├── keys-complete.ditamap         (NEW: Key Definition Map — prodname, URL, UI labels)
        │   ├── ditamap-complete.ditamap      (reltable, chunk="split", references keys-complete)
        │   ├── bookmap-complete.ditamap      (<chapter>, <bookmeta>, <navtitle> elements)
        │   └── ditaval-complete.ditaval      (multi-attribute filtering)
        ├── assets/                           (NEW: copy-and-fill templates)
        │   ├── templates/
        │   │   ├── concept-template.dita
        │   │   ├── task-template.dita
        │   │   ├── reference-template.dita
        │   │   ├── troubleshooting-template.dita
        │   │   ├── glossentry-template.dita
        │   │   ├── keys-template.ditamap        (NEW: starter Key Definition Map)
        │   │   ├── ditamap-template.ditamap
        │   │   └── bookmap-template.ditamap
        │   ├── schematron/
        │   │   └── dita-style-rules.sch         (NEW: Schematron rules for content style checks)
        │   └── vscode/
        │       └── dita.code-snippets       (VS Code snippet file for DITA elements)
        └── scripts/
            ├── validate-dita.sh             (wraps DITA-OT `dita --format=validate`)
            ├── check-conrefs.sh             (scans for broken conref/conkeyref targets)
            └── check-keys.sh               (audits keyref/keydef completeness)
```

---

## 4. MCP Server + Skill Integration — Architecture

### Your question: "How do a vector DB and a skill go together? Do you plan to add the MCP server to the skill?"

**Short answer:** The skill and MCP server are separate components that Claude uses together. The skill handles **workflows and authoring patterns**; the MCP server handles **spec lookup and element reference**. The skill's SKILL.md tells Claude when to call the MCP tools.

### How it works in practice

```
User: "What attributes can I use on <topicref>?"
  │
  ├─→ Skill triggers (DITA topic mentioned)
  │     └─→ SKILL.md loads → Claude knows the authoring workflow
  │
  └─→ Claude calls MCP tool: lookup_dita_element("topicref")
        └─→ Vector DB returns element definition, attributes, content model
              └─→ Claude combines: workflow (from skill) + spec detail (from MCP)
```

The skill does NOT contain the MCP server. The MCP server is:
1. **Configured separately** in `.claude/settings.json` under `mcpServers`
2. **Referenced by name** in SKILL.md — Claude learns it should use the tool

### What to put in SKILL.md (MCP reference section)

```markdown
## DITA Spec Lookup (MCP)

When element details are needed beyond `references/common-elements.md`,
use the MCP tool `lookup_dita_element` if the dita-spec server is available.

- Element attributes: `lookup_dita_element("topicref")`
- Content model (what goes inside): `lookup_dita_element("task content model")`
- 2.0 changes for an element: `lookup_dita_element("substeps DITA 2.0")`

If the MCP server is not configured, consult `references/common-elements.md`
for the 35 most-used elements and direct the user to dita-lang.org/1.3/ for full spec.
```

### Sources to crawl for the vector DB

Use your MCP builder to crawl these — in priority order:

| Priority | Source | Content | Format |
|---|---|---|---|
| 1 | `https://dita-ot.org/dev/` | DITA-OT 4.x docs: commands, parameters, errors, output formats | HTML |
| 2 | `https://docs.oasis-open.org/dita/dita/v1.3/errata02/os/complete/part3-all-inclusive/` | DITA 1.3 full spec (rendered HTML) | HTML |
| 3 | `https://dita-lang.org/1.3/` | DITA 1.3 language reference (individual element pages) | HTML |
| 4 | `https://dita-lang.org/2.0/` | DITA 2.0 draft spec (for 2.0-ready rules) | HTML |
| 5 | `https://www.dita-ot.org/dev/topics/output-formats.html` | Output format parameters | HTML |

**Chunking recommendation for crawl:** Chunk by individual element reference page, not by chapter — this gives one vector entry per element, which produces much more precise lookups.

**Suggested MCP tool names** (to reference in SKILL.md):
- `lookup_dita_element(element_name)` — returns definition, attributes, content model
- `search_dita_spec(query)` — semantic search across full spec
- `lookup_dita_ot_error(error_code_or_message)` — returns error explanation + fix
- `compare_dita_versions(element_name)` — returns 1.3 vs 2.0 differences for an element

---

## 5. SKILL.md Structure

### Frontmatter

```yaml
---
name: dita-authoring
description: This skill should be used when the user asks to "write a DITA topic",
  "create a DITA concept", "write a DITA task", "create a DITA reference topic",
  "build a DITA map", "design a DITA content architecture", "create DITA templates",
  "fix DITA validation errors", "review DITA content", "advise on DITA content
  strategy", "start a DITA content project", "migrate content to DITA",
  "check DITA 2.0 compatibility", "fix a broken link in a DITA map",
  "refactor this concept into a task", "apply a profiling attribute",
  "add a keyref", "create a key definition map", "set up DITA keys",
  "clean up inherited DITA content", "restructure a DITA map",
  or mentions DITA elements, conref, keyref, ditaval, substeps,
  navtitle, chunking, schematron, or DITA-OT.
version: 0.1.0
---
```

### Body sections (~1,800 words, imperative form)

1. **Purpose + Constraint** — what this skill does (3 sentences) + the 2.0-ready constraint
2. **Before You Start** — VS Code setup checklist (link to `vscode-dita-setup.md`)
3. **Topic Types Quick Reference** — table: type → root element → use-when
4. **Forward-Compatibility Rules** — brief summary of Rules A–D (details in `compatibility-2.0.md`)
5. **Key Definition Maps** — always define a `keys.ditamap`; three patterns (text var, URL, UI label); how to reference keys in topics with `<ph>`, `<xref>`, `<uicontrol>`
6. **Starting a New Content Project** — map structure first, then key map, then topics; project file option for publishing config
7. **Using Templates** — how to copy from `assets/templates/`, fill in structure, validate
8. **Content Reuse Patterns** — conref vs. keyref vs. conkeyref quick decision table; link to `content-reuse.md`
9. **Structural Refactoring** — how to fix broken maps, convert topic types, apply profiling, clean up inherited content; reltable-over-`<related-links>` rule; run `validate-dita.sh --schematron`
10. **Validation Workflow** — Stage 1 structural → Stage 2 Schematron → `check-keys.sh` → `check-conrefs.sh`
11. **DITA Spec Lookup (MCP)** — how to use MCP tools; fallback to `common-elements.md`
12. **Additional Resources** — full index of all `references/`, `examples/`, `assets/` files

---

## 6. References Files Plan

| File | Content | ~Size | Audience |
|---|---|---|---|
| `topic-types.md` | Full XML structure per type, required vs. optional elements, beginner mistakes, 2.0-ready patterns | 3,000 words | Beginner–intermediate |
| `maps-and-bookmaps.md` | Map hierarchy, `<topicref>` attributes, **reltable strategy** (prefer reltables over `<related-links>` in topics — keeps topics portable; full annotated reltable walkthrough), chunking (`split`/`combine`), map-to-map, key definition maps | 2,500 words | Intermediate |
| `content-reuse.md` | Conref syntax, conkeyref, keyref for variables/links, push conref; **Key Definition Maps** — product names, UI labels, external URLs as keys; conref vs. keyref decision guide; reuse architecture patterns; what not to reuse | 2,500 words | Intermediate |
| `metadata-filtering.md` | Profiling attributes, DITAVAL syntax, conditional processing, metadata strategy for content projects | 1,500 words | Beginner–intermediate |
| `dita-ot-reference.md` | **RENAMED from dita-ot-processing.md** — DITA-OT pipeline stages, `dita` CLI commands, output formats, common validation errors with fixes; **DITA-OT 4.x project files** (`dita-project.xml` / `.yaml`): syntax, when to write one, what it defines (inputs, outputs, filters); Schematron integration via `args.validate.ignore.url`; validate subcommand | 3,000 words | Intermediate |
| `content-strategy.md` | Minimalism principles, topic granularity decisions, project setup checklist, localization prep, single-sourcing ROI | 2,000 words | Beginner–intermediate |
| `vscode-dita-setup.md` | User's installed extensions: **RedHat XML** (schema validation, DTD-based auto-complete, XML catalog) + **DitaCraft** (DITA-aware snippets, topic navigation, map tree view); configuration for both; XML catalog setup to resolve DITA DTDs locally; using the bundled `.code-snippets` file; recommended folder structure | 1,500 words | Beginner |
| `compatibility-2.0.md` | Full DITA 2.0 Readiness Checklist with before/after XML for all four rules; what 2.0 removes, what it adds; reviewing existing content for 2.0 issues | 2,500 words | Intermediate |
| `common-elements.md` | Curated quick-ref: purpose, parent context, required attributes, 2.0-ready note for ~35 most-used elements. MCP fallback when server not configured. | 2,000 words | Beginner |

---

## 7. Examples Files Plan

All examples must be valid DITA 1.3 and follow the four forward-compatibility rules.

| File | Demonstrates | 2.0-Ready Notes |
|---|---|---|
| `concept-complete.dita` | `<concept>`, `<conbody>`, `<section>`, `<image>` | `<alt>` inside `<image>`, `<shortdesc>` |
| `task-complete.dita` | `<task>`, `<steps>`, `<step>`, `<cmd>`, `<uicontrol keyref="..."/>` | Nested `<steps>` instead of `<substeps>`; UI label via keyref |
| `reference-complete.dita` | `<refbody>`, `<table>`, `<simpletable>`, `<properties>` | `<shortdesc>` not `<abstract>` |
| `troubleshooting-complete.dita` | `<condition>`, `<troubleSolution>`, `<cause>`, `<remedy>` | Standalone topic, not embedded in task |
| `keys-complete.ditamap` | **Key Definition Map** — product name, external URL, UI label keys | The "Pro" pattern: all variables and external links defined centrally |
| `ditamap-complete.ditamap` | Nested `<topicref>`, `<reltable>`, `<mapref>` to `keys-complete` | `chunk="split"` only; `<navtitle>` element; references Key Definition Map |
| `bookmap-complete.ditamap` | `<bookmap>`, `<chapter>`, `<appendix>`, `<bookmeta>` | `<navtitle>` in `<topicmeta>` |
| `ditaval-complete.ditaval` | Multi-attribute filtering: audience + product + platform | Standard 1.3/2.0 compatible syntax |

### `keys-complete.ditamap` — Key Definition Map example

Provided by domain expert. Demonstrates three key definition patterns:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA Map//EN" "map.dtd">
<map>
  <title>Minicourse Keys</title>

  <!-- Pattern 1: Text variable (product name, version numbers) -->
  <keydef keys="prodname">
    <topicmeta>
      <keywords>
        <keyword>DITA Masterclass 101</keyword>
      </keywords>
    </topicmeta>
  </keydef>

  <!-- Pattern 2: External URL with link text -->
  <keydef keys="url-dita-ot" href="https://www.dita-ot.org/" format="html" scope="external">
    <topicmeta>
      <linktext>Official DITA-OT Documentation</linktext>
    </topicmeta>
  </keydef>

  <!-- Pattern 3: UI label (button text, menu item) -->
  <keydef keys="btn-save">
    <topicmeta>
      <keywords>
        <keyword>Commit Changes</keyword>
      </keywords>
    </topicmeta>
  </keydef>
</map>
```

Usage in a topic (`task-complete.dita`):

```xml
<title>Setting up <ph keyref="prodname"/></title>
<shortdesc>Follow these steps to initialize your environment for <ph keyref="prodname"/>.</shortdesc>
...
<cmd>Download the processor from the <xref keyref="url-dita-ot"/>.</cmd>
...
<cmd>Click <uicontrol keyref="btn-save"/>.</cmd>
```

**Why this pattern matters for the skill:**
- Product names and UI labels are defined once; change propagates everywhere
- External URLs are never hardcoded in topic files — only in the key map
- `check-keys.sh` validates that every `keyref` in topics is backed by a definition here

---

## 8. Assets: Templates

Templates in `assets/templates/` are **copy-and-fill starters** — minimal valid DITA with placeholder comments. Different from `examples/` (which are fully authored and annotated).

Each template includes:
- Correct DOCTYPE declaration for DITA 1.3
- Required elements skeleton with `<!-- FILL: ... -->` comments
- The `@xml:lang` attribute (localization readiness)
- An `@id` attribute placeholder
- A `<shortdesc>` slot

### `ditamap-template.ditamap` — RelTable skeleton

The map template includes a **commented-out reltable skeleton** so Claude always has the correct column/row structure to hand. Reltable syntax is non-intuitive; the skeleton removes guesswork and enforces the "map-level links, not topic-level `<related-links>`" best practice.

```xml
<!--
  RELATIONSHIP TABLE SKELETON — uncomment and fill to define related-links at map level.
  Never hardcode <related-links> inside topics; manage links here instead.
  Each <relrow> creates bidirectional links between topics in different <relcell> columns.

  <reltable title="Related topics">
    <relheader>
      <relcolspec type="concept"/>
      <relcolspec type="task"/>
      <relcolspec type="reference"/>
    </relheader>
    <relrow>
      <relcell>
        <topicref href="concepts/c_your_concept.dita"/>
      </relcell>
      <relcell>
        <topicref href="tasks/t_your_task.dita"/>
      </relcell>
      <relcell>
        <topicref href="reference/r_your_reference.dita"/>
      </relcell>
    </relrow>
  </reltable>
-->
```

**How reltables work:** Each `<relrow>` creates bidirectional "Related topics" links between all topics in different `<relcell>` columns. A concept in column 1 and a task in column 2 in the same row will link to each other in the output. Topics can appear in multiple rows; links accumulate. Topics in the same `<relcell>` do **not** link to each other by default.

### `assets/vscode/dita.code-snippets`

A VS Code snippet file users can copy to `.vscode/` in their project. Provides tab-expanded starters for:
- `dita-concept` → concept topic skeleton
- `dita-task` → task topic with steps
- `dita-step` → a single `<step>` with `<cmd>`
- `dita-nested-steps` → pattern for replacing substeps
- `dita-image` → `<image>` with `<alt>` element (not attribute)
- `dita-keyref` → `<ph keyref="..."/>` pattern
- `dita-keydef-text` → `<keydef>` with `<keyword>` for text variables
- `dita-keydef-url` → `<keydef>` with `href` + `<linktext>` for external URLs
- `dita-keydef-ui` → `<keydef>` with `<keyword>` for UI labels
- `dita-conref` → conref attribute pattern
- `dita-note` → `<note type="...">` variants
- `dita-reltable` → full reltable skeleton (3 columns: concept/task/reference)

---

## 8. Scripts Plan

### `validate-dita.sh`

Two-stage validation: **Stage 1** checks XML structure (DTD/Schema — "is this legal DITA?"); **Stage 2** runs Schematron rules ("does this content follow style standards?"). This makes Claude an editor, not just a parser.

```bash
#!/bin/bash
# validate-dita.sh: Two-stage DITA validation
# Stage 1: DTD/Schema (structural validity via DITA-OT)
# Stage 2: Schematron (content style rules via Saxon/DITA-OT Schematron plugin)
# Usage: ./validate-dita.sh <map.ditamap> [--schematron]

INPUT="${1:?Usage: validate-dita.sh <input.ditamap> [--schematron]}"
SCHEMATRON_RULES="$(dirname "$0")/../assets/schematron/dita-style-rules.sch"

echo "=== Stage 1: Structural validation (DTD/Schema) ==="
dita --format=validate --input="$INPUT"
STAGE1_EXIT=$?

if [[ "$2" == "--schematron" ]]; then
  echo ""
  echo "=== Stage 2: Content style rules (Schematron) ==="
  if command -v java &>/dev/null && [ -f "$SCHEMATRON_RULES" ]; then
    # Requires Saxon-HE on classpath or use dita-ot schematron plugin
    java -jar saxon.jar -s:"$INPUT" -xsl:"$SCHEMATRON_RULES" 2>&1
  else
    echo "SKIP: Saxon or schematron rules not found. See assets/schematron/dita-style-rules.sch"
  fi
fi

exit $STAGE1_EXIT
```

**Schematron distinguishes two validation layers:**

| Layer | Tool | Catches | Example rule |
|---|---|---|---|
| Structural | DITA-OT (`--format=validate`) | Illegal XML, missing required elements, broken references | Missing `<cmd>` in `<step>` |
| Style/editorial | Schematron (`.sch` file) | Content policy violations, style guide rules | `<shortdesc>` over 50 words; task missing an image |

**`assets/schematron/dita-style-rules.sch`** — starter Schematron rules bundled in the skill:
- Every `<task>` must have a `<shortdesc>`
- `<shortdesc>` word count ≤ 50 words
- Every `<step>` must have a `<cmd>`
- No bare text in `<taskbody>` (outside `<steps>`)
- Every `<image>` must have an `<alt>` child (Rule A check)
- No `<substeps>` present (Rule B check)
- No `navtitle` attribute used (Rule A check)
- `chunk` attribute value must be `split` or `combine` (Rule C check)
- No `<object>` used for multimedia (Rule D check)
- No `<related-links>` inside topics (map-level reltable rule)

This means Schematron **enforces the four 2.0-ready rules and the reltable best practice automatically** on existing and new content.

### `check-conrefs.sh`
Scans `.dita` files for `conref`/`conkeyref` attributes. Verifies every target file exists and every target element ID exists in that file. Reports file + line for each broken reference.

### `check-keys.sh`
Audits keyref completeness — every `keyref` used in topics must have a `keys=` definition in the map.
```bash
#!/bin/bash
MAP_FILE="${1:?Usage: check-keys.sh <main-map.ditamap>}"
echo "Checking defined keys in $MAP_FILE..."
grep -oP '(?<=keys=")[^"]+' "$MAP_FILE" | sort | uniq > defined_keys.tmp
echo "Checking topics for undefined keyrefs..."
find . -name "*.dita" -exec grep -oP '(?<=keyref=")[^"]+' {} + \
  | awk -F: '{print $2}' | sort | uniq > used_keys.tmp
comm -13 defined_keys.tmp used_keys.tmp > broken_keys.tmp
if [ -s broken_keys.tmp ]; then
  echo "CRITICAL: Keys used but not defined in map:"
  cat broken_keys.tmp; rm *.tmp; exit 1
else
  echo "SUCCESS: All keyrefs valid."; rm *.tmp
fi
```

---

## 9. DITA Language Reference — Recommendation

**Source:** `https://github.com/dita-lang/dita-lang.org/tree/main/1.3/dita`

**Verdict: Do NOT embed in skill references.** Hundreds of DITA XML source files (~1,500 pages). Wrong format for skill use (raw XML, not prose).

**Recommended approach:**

| Need | Tool | Why |
|---|---|---|
| "What attributes does `<step>` take?" | **MCP vector DB** | Semantic search over HTML element pages; precise per-element lookup |
| "What can appear inside `<task>`?" | **MCP vector DB** or graph | Content models from spec; graph gives structural traversal |
| Specialization inheritance queries | **Graph** | Base→specialized element chains are pure graph relationships |
| Quick element lookup (35 most common) | **`common-elements.md`** | Always in context; no MCP call needed |
| DITA-OT error codes | **MCP vector DB** | Crawl dita-ot.org error reference pages |

**What to crawl** (see Section 4 for full list with URLs).

---

## 10. Implementation Steps

1. **Create plugin structure** — directories, `plugin.json`, `README.md`
2. **Write assets** — 8 topic/map templates + `dita.code-snippets` (with 3 keydef snippets) + `dita-style-rules.sch`
3. **Write scripts** — `validate-dita.sh` (two-stage), `check-conrefs.sh`, `check-keys.sh`
4. **Write examples** — 8 fully authored files; `keys-complete.ditamap` first (it's referenced by `task-complete.dita` and `ditamap-complete.ditamap`)
5. **Write reference files** in this order:
   - `compatibility-2.0.md` (anchors the version strategy + Schematron connection)
   - `content-reuse.md` (Key Definition Maps are a beginner priority)
   - `vscode-dita-setup.md` (beginner entry point)
   - `topic-types.md`, `common-elements.md` (MCP fallback)
   - `maps-and-bookmaps.md`, `metadata-filtering.md`
   - `dita-ot-reference.md` (4.x project files + Schematron integration)
   - `content-strategy.md`
6. **Write SKILL.md** — lean, imperative, Key Def Maps section, Schematron in validation workflow, structural refactoring section, MCP reference section, resource index
7. **Validate structure** — checklist from `skill-development/SKILL.md`
8. **Test** — trigger with beginner questions, structural fix requests, key map requests; verify 2.0-ready output and Schematron script runs
9. **Iterate** — refine trigger phrases and references based on real usage

---

## 11. Summary

| Component | Count | Notes |
|---|---|---|
| SKILL.md | 1 | ~1,800 words, 12 sections, imperative form |
| Reference files | 8 | `dita-ot-reference.md` covers 4.x project files; `content-reuse.md` covers Key Def Maps |
| Example files | 8 | Added `keys-complete.ditamap` with all three key definition patterns |
| Template assets | 8 DITA + 1 snippets file | Added `keys-template.ditamap`; snippets include 3 keydef patterns |
| Schematron | 1 `.sch` file | Enforces all four 2.0-ready rules + style standards (shortdesc length, etc.) |
| Scripts | 3 | `validate-dita.sh` (two-stage: DTD + Schematron), `check-conrefs.sh`, `check-keys.sh` |
| MCP integration | External | Skill references MCP tools by name; user configures server separately |

**MCP sources to crawl (priority order):** dita-ot.org/dev → DITA 1.3 spec HTML → dita-lang.org/1.3 → dita-lang.org/2.0

**DITA-OT 4.x project files scope note:** `dita-ot-reference.md` covers writing `dita-project.xml` / `.yaml` files (what inputs/outputs/filters to define). This is content architecture knowledge, not build pipeline automation. The full CI/CD pipeline setup remains out of scope.

**Plan file:** `/home/hpz440/projects/dita-skill-plan.md`
