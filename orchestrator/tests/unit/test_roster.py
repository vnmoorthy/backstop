"""The agent roster is real, covers the lifecycle, and uses every sponsor."""
from __future__ import annotations

from backstop.agents.roster import PHASES, ROSTER, built_count, by_phase, sponsors_used


def test_roster_has_a_full_workforce():
    # the product promise: 15-20+ specialized agents
    assert len(ROSTER) >= 20
    # ids are unique
    assert len({a.id for a in ROSTER}) == len(ROSTER)


def test_every_phase_is_staffed():
    for phase in PHASES:
        assert by_phase(phase), f"no agent staffs phase {phase!r}"


def test_all_eight_sponsors_are_load_bearing():
    used = set(sponsors_used())
    assert {"PAVO", "Moss", "TrueFoundry", "Unsiloed", "MiniMax", "Qwen", "LiveKit", "AWS"} <= used


def test_most_agents_are_actually_built_today():
    # honesty: a clear majority are backed by a real service/adapter now
    assert built_count() >= 15
