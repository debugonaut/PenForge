"""Penforge Latin v1: every listed check must execute; FAIL/SKIP/ERROR block."""

PROFILE = {
    "sections": {
        "Penforge Latin v1": [
            "required_tables",
            "sfnt_version",
            "name/char_restrictions",
            "os2_metrics_match_hhea",
        ]
    }
}
