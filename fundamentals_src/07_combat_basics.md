---
title: Combat Basics (how a battle actually plays out — tactical layer)
category: combat
---

# Combat Basics

This is the **tactical layer** — what happens once two armies actually collide in a province. It is
distinct from `fundamentals:war_and_expansion`, which covers the strategic layer (whether to declare
war, warscore, peace deals, AE/coalition risk). Use this doc when a question is about how a battle
resolves, not whether/when to fight one.

## The 3-day Fire/Shock cycle

A battle is a sequence of 3-day phases alternating **Fire** then **Shock** (Fire first). Each phase, both
sides roll dice determining morale damage and casualties for that phase. **Infantry** is strongest during
Fire, **cavalry** is strongest during Shock, and **artillery** grows into a powerful Fire-phase unit as
military technology advances (up to +8.4 cumulative fire bonus by tech 32). This is why army composition
and unit tech matter more than raw headcount — an army badly mismatched to the current tech era can lose
despite equal or greater numbers.

## Combat width and reserves

**Combat width** caps how many regiments can actively fight at once (front row + back row); it starts
around 15-20 depending on military technology and rises over the game. Units beyond that cap sit in
**reserve** — they don't fight but still take passive morale damage, and can rotate in as space opens.
Overstacking an army far beyond combat width mostly wastes troops sitting in reserve rather than adding
combat power.

## Flanking

A unit can attack an enemy at its flank (not just directly across) if its **flanking range** is high
enough — base range is 1 for infantry, 2 for cavalry/artillery, both increasing with military technology.
This is part of why cavalry and artillery punch above a naive "just compare numbers" expectation, and why
having *some* front-row width advantage over the enemy matters even without an overall size advantage.

## Terrain and crossing penalties

Attacking into unfavorable terrain (mountains, forests, marsh, etc.) applies an attacker penalty to dice
rolls; defending in it is correspondingly strong — this is the mechanical basis for "defend in mountains
when outnumbered" advice. Crossing a river costs the attacker -1 to all rolls, crossing a strait or making
an amphibious landing costs -2 — unless the attacking leader's maneuver stat exceeds the defender's, which
cancels the crossing penalty entirely.

## Morale, discipline, and leaders

Morale determines when a regiment breaks and routs — losing morale from casualties and passive damage
matters as much as the casualties themselves. **Discipline** is a national modifier that scales both
damage dealt and damage received in favor of the higher-discipline side, making it one of the highest-
value military stats to invest in. Generals/admirals grant "pips" (fire/shock/morale/maneuver) that
directly add to dice rolls and reduce losses — a skilled leader can noticeably swing an otherwise even
battle.

## See also

`wiki:w-350a689b97` (Land warfare — full combat width/terrain/crossing tables), `wiki:w-257f8d675a`
(Land units — unit types and the cavalry-to-infantry ratio cap, see `fundamentals:war_and_expansion` for
how exceeding it is penalized), `wiki:w-14877e80cb` (Discipline), `wiki:w-67ab83586a` (Naval warfare —
the same Fire/Shock structure applies at sea with different unit roles).
