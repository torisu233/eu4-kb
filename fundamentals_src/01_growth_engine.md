---
title: Growth Engine (Development, Monarch Power, Technology, Ideas, Institutions)
category: growth_engine
---

# Growth Engine

The five things that determine how strong a country becomes over time, and how they connect to each
other. This is the first thing to check when a question is about "how do I get stronger/richer/more
advanced" in the abstract.

## Development

A province's **development** is the sum of its base tax + base production + base manpower. It is the
single most-used size metric in the game: mission/decision requirements, government rank thresholds,
overextension, coring cost, and institution spread are almost all keyed off development, not raw
province count. Development is raised by spending monarch power directly on a province (ADM raises
tax, DIP raises production, MIL raises manpower).

## Monarch Power (ADM/DIP/MIL)

Monarch power is generated every month and spent on almost every meaningful national action.
**Administrative** funds coring, stability, inflation reduction, and administrative tech/ideas.
**Diplomatic** funds diplomatic/naval tech, unjustified peace demands, annexing subjects, and culture
conversion. **Military** funds military tech, recruiting leaders, and war-related actions. Generation
comes from ruler skill, one advisor per type, "national focus" (biases +2/month into one type at
-1/month from each other — cooldown depends on Government Rank, see `fundamentals:internal_governance`),
and power projection ≥50 (+1/month to all three).

## Technology

Three lines (ADM/DIP/MIL) mirroring the three power types, each with up to 33 levels. Each level is
associated with a real historical year and unlocks buildings/units/abilities. A country's **technology
group** sets its starting level and, for several non-Western groups, a starting cost penalty. Tech cost
is heavily affected by Institutions (below) — checking institution embrace is usually more important
than anything else when a "why is my tech so expensive" question comes up.

## Ideas: National Ideas vs Idea Groups

Every country picks up to 8 **Idea Groups** from a shared pool of ~25 (Administrative/Diplomatic/
Military categories), unlocked progressively by technology. Separately, every country also has its own
fixed **National Ideas** (7 bonuses + 1 ambition) — a genuinely unique hand-written set for ~30-40 major
nations, and a shared, less powerful default set for everyone else. Whether a specific National Ideas
set is available to a "generic" nation the user is asking about is exactly the kind of question the
content-layering rules in `fundamentals:content_layering_and_reachability` are for.

## Institutions

**Institutions** are the primary determinant of technology cost, and the single most common trap for
non-European/non-Western nations. There are 8 institutions total, appearing roughly every 50 years from
1450 to 1750 (the first, Feudalism, already exists in most of the world in 1444). An institution spreads
into a province at random each year based on eligible neighboring provinces; **embracing** it requires
it to be present in provinces holding ≥10% of your development, plus a ducat cost for the rest. Until
embraced, technologies gated behind that institution get an escalating penalty (+15% for the first tech,
+30% for the next, +50% for all further ones) — and this **stacks across multiple un-embraced
institutions**. A country that ignores institution spread for too long can find every tech level
carrying a 100%+ cost penalty; this is usually the real answer to "why does everything feel so
expensive" for a nation outside Western Europe.

## See also

`wiki:w-26a08bd821` (Monarch power, full cost tables), `wiki:w-d018b082e8` (Technology, tech groups
table), `wiki:w-dcd439a732` (Institutions, exact spread modifiers and embrace cost formula),
`wiki:w-610329f547` (Idea groups), `wiki:w-49088b9867` (National ideas), `wiki:w-4c17aadf51`
(Development).
