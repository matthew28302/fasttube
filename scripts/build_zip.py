import os
import zipfile
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
zip_path = root_dir / "fasttube_deploy.zip"

if zip_path.exists():
    zip_path.unlink()

include_roots = [
    "app",
    "requirements.txt",
    ".env",
    ".env.example",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
]

exclude_names = {
    "__pycache__",
    ".git",
    ".venv",
    ".pytest_cache",
    ".DS_Store",
    "Thumbs.db",
}

exclude_exts = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".zip",
    ".log",
}

count = 0
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for item_name in include_roots:
        p = root_dir / item_name
        if not p.exists():
            continue
        if p.is_file():
            arcname = p.name
            zf.write(p, arcname=arcname)
            count += 1
            print(f"Added file: {arcname}")
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in exclude_names and not d.startswith(".")]
                for file in files:
                    if any(file.endswith(ext) for ext in exclude_exts):
                        continue
                    if file.startswith("."):
                        continue
                    file_path = Path(root) / file
                    rel_path = file_path.relative_to(root_dir)
                    arcname = rel_path.as_posix()
                    zf.write(file_path, arcname=arcname)
                    count += 1
                    print(f"Added: {arcname}")

print(f"\nBuild completed: {zip_path.name} ({count} files, {zip_path.stat().st_size / 1024:.1f} KB)")
