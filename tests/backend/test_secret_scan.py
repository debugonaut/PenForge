"""Placeholders are inert, while real-looking credentials still block CI."""

from scripts import check_secrets


def test_scan_accepts_exact_placeholder_and_detects_credentials_without_echoing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(check_secrets, "ROOT", tmp_path)
    source = tmp_path / "runbook.md"
    source.write_text("postgresql+psycopg://app:CHANGE_ME@db.example/postgres")
    assert check_secrets.main() == 0
    key = "AKIA" + "1234567890123456"
    password = "penforge-really-private"
    source.write_text(key + "\n" + "postgresql://app:" + password + "@db.example/postgres")
    assert check_secrets.main() == 1
    output = capsys.readouterr().err
    assert "aws_access_key" in output and "neon_password_url" in output
    assert key not in output and "penforge-really-private" not in output
