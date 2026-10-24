"""Optional native PostgreSQL 16.2 tool for local development when Docker is unavailable.

Only a private Unix socket is opened. Never call the pgserver convenience manager,
which may create global runtime directories. No extensions are installed or used.
"""

import argparse
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    try:
        from pgserver._commands import POSTGRES_BIN_PATH
    except ImportError:
        raise SystemExit(
            "Install optional group: uv sync --locked --group dev --group local-postgres"
        ) from None
    root = Path(__file__).resolve().parents[1]
    data = root / ".local/postgres-native"
    # Deterministic user-private short path avoids Unix-socket path length limits.
    socket = (
        Path("/private/tmp" if Path("/private/tmp").exists() else "/tmp")
        / f"penforge-pg-{os.getuid()}"
    )
    if socket.is_symlink() or (socket.exists() and socket.stat().st_uid != os.getuid()):
        raise SystemExit("Unsafe PostgreSQL socket directory")
    socket.mkdir(mode=0o700, exist_ok=True)
    socket.chmod(0o700)
    data.parent.mkdir(parents=True, exist_ok=True)

    def run(tool: str, *arguments: str, check: bool = True):
        return subprocess.run([str(POSTGRES_BIN_PATH / tool), *arguments], check=check)

    if args.action == "stop":
        run("pg_ctl", "-D", str(data), "-m", "fast", "stop")
        return
    if not (data / "PG_VERSION").exists():
        run(
            "initdb",
            "-D",
            str(data),
            "--auth-local=trust",
            "--auth-host=reject",
            "--encoding=UTF8",
            "-U",
            "penforge",
        )
    if run("pg_ctl", "-D", str(data), "status", check=False).returncode != 0:
        run(
            "pg_ctl",
            "-D",
            str(data),
            "-l",
            str(root / ".local/postgres.log"),
            "-o",
            f'-h "" -k {socket} -p 55432',
            "start",
        )
    # Local superuser exists only inside this disposable developer-owned cluster.
    query = subprocess.run(
        [
            str(POSTGRES_BIN_PATH / "psql"),
            "-h",
            str(socket),
            "-p",
            "55432",
            "-U",
            "penforge",
            "-d",
            "postgres",
            "-Atc",
            "SELECT datname FROM pg_database WHERE datname IN ('penforge','penforge_test')",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    for database in ("penforge", "penforge_test"):
        if database not in query.stdout.splitlines():
            run("createdb", "-h", str(socket), "-p", "55432", "-U", "penforge", database)
    print(f"DATABASE_URL=postgresql+psycopg://penforge@/penforge?host={socket}&port=55432")
    print(
        f"TEST_DATABASE_URL=postgresql+psycopg://penforge@/penforge_test?host={socket}&port=55432"
    )


if __name__ == "__main__":
    main()
