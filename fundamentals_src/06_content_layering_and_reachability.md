---
title: Content Layering and Reachability (how to judge generic vs nation-exclusive content)
category: layering_rules
---

# Content Layering and Reachability

This document is written for the AI answering questions, not for players. It explains how EU4's
content (missions, national ideas, decisions, some government reforms) is organized in layers, and
gives an executable procedure for judging whether a piece of retrieved content actually applies to a
question about a *generic/hypothetical* nation (e.g. "an ordinary Sunni monarchy") rather than one
specific named country.

## Why this matters

A retrieved chunk can be semantically relevant to a query (it mentions the right religion, government
type, or mechanic) while actually being **exclusive to one specific country tag**. If that distinction
is not checked, the answer will present country-exclusive content as if any qualifying nation could
use it — which is wrong and misleading.

## The three layers

| Layer | Who gets it | How the game data marks it | How wiki pages usually phrase it |
| --- | --- | --- | --- |
| 1. Generic default | All ~974 country tags that have no override | Mission slots have `generic = yes`; no `tag =` restriction in `potential` | "Generic missions", "by default if nothing else applies" |
| 2. Shared conditional pool | A group of nations sharing a broad property | `potential` restricted by `religion`, `culture_group`, `technology_group`, `capital` region, or `government` type — **not** a specific `tag` | "Capital is in the Horn of Africa region", "Religion is Muslim, Culture is in Levantine group" |
| 3. Nation-exclusive | One (or a short fixed list of) specific country tag(s) | `potential = { tag = XXX }` (or `OR = { tag = A tag = B }` for a short fixed list) | The content appears only on that nation's own wiki page, or a "Nation specific missions" table entry |

Layer 2 **overrides** layer 1 for nations that qualify (the generic slot is replaced), and layer 3
overrides both layer 1 and layer 2 for the specific tag(s). A real example straight from the wiki's
own `Missions` overview page: *"Horn of Africa missions... Capital is in Horn of Africa region, except
Ethiopia and Ajuuraan... Replaces the generic missions."* — this is a layer-2 pool, shared by several
minor nations in that region, not exclusive to any single one of them.

## Reachability of layer-3 (nation-exclusive) content

Even confirmed nation-exclusive content splits into two cases:

- **Reachable via formation/reformation**: a "Form X" decision or mission exists whose `potential`
  requirement is itself a **broad, layer-2-style condition** (e.g. `culture_group = turko_semitic` to
  form Arabia, not `tag = <single nation>`). Any starting nation that meets that broad condition can
  switch to tag X (`change_tag`) and thereby gain access to X's exclusive content. When you find this,
  say so explicitly and name the condition — do not silently treat the exclusive content as directly
  available, and do not silently discard it either.
- **Not reachable**: no formation decision exists for that tag, or the only paths that exist are
  themselves gated by an equally narrow, single-tag condition. In this case the content is only
  available to a save that starts as that exact nation. Say so plainly.

Do not assume reachability either way without checking — always look for a `decisions/*Nation.txt`-style
formation decision (or a mission/event doing the same) before concluding either "anyone can get this"
or "no one else can get this".

## Procedure: answering a question about a generic/hypothetical nation

Use this when the question describes a nation by **properties** (religion, government type, region,
culture, tech group — e.g. "an ordinary Sunni monarchy") rather than naming a specific tag.

1. **Classify the question.** Is the nation described by properties, or is it a specific named
   country? If it names a specific country, skip this procedure — just answer using that country's
   own content directly (including its exclusive content).
2. **Retrieve normally** (search_kb / grep_kb as usual).
3. **Screen every candidate result for its scope**, before using it:
   - Comes from a mechanic/overview page and is described as generic/universal → safe to use directly.
   - Comes from a page whose title is a specific country/dynasty name, or the retrieved text carries
     a `tag = XXX` condition, or wiki language like *"unique to"* / *"only available to"* → **do not**
     present this as something the described nation gets by default. Check reachability (see above)
     before deciding how — or whether — to mention it at all.
   - Wording like *"any nation with"* / *"nations that have"* / *"if your capital is in"* / a named
     religion, culture group, or tech group (not a single tag) → this is a layer-2 shared pool; it
     *does* apply to the described nation if the properties match, treat it as directly usable.
4. **When unsure whether a match is layer-2 (shared) or layer-3 (exclusive)**, prefer the cautious
   reading: state the uncertainty rather than asserting the content is generally available.
5. **Structure the final answer in explicit tiers**, don't merge them into one flat list:
   - What every nation matching the description can do (layer 1 + qualifying layer 2 pools).
   - What requires an extra step to unlock (formation decision — name it and its requirement).
   - What is simply out of reach for this kind of nation (name why, if useful context).

## Quick signal reference

| Signal seen in retrieved text | Likely layer |
| --- | --- |
| Page title is a specific country or dynasty name | 3 (exclusive) — verify reachability |
| `potential = { tag = XXX }` in a game_file chunk | 3 (exclusive) |
| `generic = yes` in a game_file chunk | 1 (generic, can be overridden) |
| "unique to", "only available to", "exclusive to" | 3 (exclusive) |
| "any nation with", "nations that have", "if your capital is in", "religion is X", "culture group is Y" | 2 (shared pool) — check whether the described nation matches |
| No conditions mentioned at all, described as a base mechanic | 1 (generic) |

See also: `wiki:w-323ea30c1d` (Missions overview, has the full generic/regional/exclusive breakdown
with real examples), `wiki:w-49088b9867` (National ideas, has the analogous generic-vs-unique split
for national idea sets), `wiki:w-610329f547` (Idea groups, the shared 25-group pool every nation picks from).
