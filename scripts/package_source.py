"""Package an explicit source allowlist, excluding local state and credentials."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent.parent
output = root / "dist" / "worldfork-0.1.0-source.zip"
output.parent.mkdir(exist_ok=True)
files = [root / name for name in (
    "README.md", "SECURITY.md", "Start-WorldFork.ps1", "requirements.txt",
    "pyproject.toml", ".gitignore", ".env.example", "LICENSE",
)]
files.extend(root / "docs" / name for name in (
    "architecture.md", "contract.md", "model-card.md", "validation.md",
))
for directory in ("worldfork", "web", "tests", "scripts"):
    files.extend(path for path in (root / directory).rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts
                 and path.suffix not in {".pyc", ".pyo"})
files.extend((root / "docs" / "screenshots").glob("*.png"))
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, Path("worldfork") / path.relative_to(root))
with ZipFile(output) as archive:
    assert archive.testzip() is None
    assert all(".worldfork" not in name.split("/") and not name.endswith("/.env")
               for name in archive.namelist())
print(f"Created {output.name}: {len(files)} source files; no local run database or credentials.")
