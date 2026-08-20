"""SCENARIO_TAGS stays in lockstep with the scenario lists: every scenario tagged,
no tags pointing at scenarios that no longer exist."""

from evals.scenarios import _ALL, SCENARIO_TAGS


def test_every_scenario_is_tagged():
    untagged = [s.name for s in _ALL if not SCENARIO_TAGS.get(s.name)]
    assert not untagged, f"scenarios missing tags: {untagged}"


def test_no_orphan_tag_entries():
    names = {s.name for s in _ALL}
    orphans = [n for n in SCENARIO_TAGS if n not in names]
    assert not orphans, f"SCENARIO_TAGS entries for unknown scenarios: {orphans}"


def test_tags_are_kebab_lowercase():
    bad = [(n, t) for n, tags in SCENARIO_TAGS.items() for t in tags
           if t != t.lower() or " " in t]
    assert not bad, f"non-kebab tags: {bad}"
