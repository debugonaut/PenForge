"""Synthetic-only opt-in line/connected-region experiment; no recognizer or glyph identities."""

from collections import deque


def experiment(
    mask,
    expected_text,
    *,
    experimental_opt_in=False,
    fixture_domain=None,
    observed_fixture_words=None,
):
    if experimental_opt_in is not True or fixture_domain != "synthetic_control":
        raise ValueError("freeform_research_opt_in_required")
    if (
        not mask
        or not mask[0]
        or len(mask) * len(mask[0]) > 1_000_000
        or any(len(row) != len(mask[0]) for row in mask)
    ):
        raise ValueError("experimental_input_invalid")
    width = len(mask[0])
    if any(type(v) is not bool for row in mask for v in row):
        raise ValueError("experimental_input_invalid")
    active = [y for y, row in enumerate(mask) if any(row)]
    runs: list[list[int]] = []
    for y in active:
        if not runs or runs[-1][-1] != y - 1:
            runs.append([])
        runs[-1].append(y)
    lines, connected = [], []
    for run in runs:
        line = [0, run[0], width, run[-1] + 1]
        lines.append(line)
        todo = {(x, y) for y in run for x in range(width) if mask[y][x]}
        while todo:
            first = min(todo, key=lambda p: (p[1], p[0]))
            todo.remove(first)
            queue, points = deque([first]), [first]
            while queue:
                x, y = queue.popleft()
                for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if p in todo:
                        todo.remove(p)
                        queue.append(p)
                        points.append(p)
            connected.append(
                [
                    min(x for x, y in points),
                    min(y for x, y in points),
                    max(x for x, y in points) + 1,
                    max(y for x, y in points) + 1,
                ]
            )
    regions = sorted(connected, key=lambda b: (b[1], b[0], b[3], b[2]))
    expected = expected_text.split()
    observed = observed_fixture_words
    mismatch = observed is not None and list(observed) != expected
    hypotheses = [
        {
            "region": region,
            "prompt_hypothesis": expected[i] if i < len(expected) else None,
            "observed_fixture_annotation": observed[i]
            if observed is not None and i < len(observed)
            else None,
            "confidence": None,
            "approved_identity": None,
            "character_boundaries": [],
            "status": "connected_region_preserved",
        }
        for i, region in enumerate(regions)
    ]
    return {
        "algorithm_version": "projection-connected-regions-v1",
        "capability_status": "research_only",
        "domain": "synthetic_control",
        "normal_creation_path": False,
        "lines": lines,
        "hypotheses": hypotheses,
        "prompt_is_ground_truth": False,
        "alignment_status": "mismatch" if mismatch else "unqualified",
        "requires_isolated_recapture": True,
        "structured_fallback": {
            "capture_type": "isolated_characters",
            "expected_codepoints": sorted(set(map(ord, expected_text)) - {32}),
            "approved_identity": None,
        },
        "recognition": None,
        "writer_fidelity": None,
        "human_preference": None,
    }
