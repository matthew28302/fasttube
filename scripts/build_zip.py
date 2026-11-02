import os
import zipfile
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
zip_path = root_dir / "fasttube_deploy.zip"

if zip_path.exists():
    zip_path.unlink()

# Complete list of files and directories required for production deployment
include_items = [
    "app",
    "scripts",
    "cookies",
    "app.py",
    ".env",
    ".env.example",
    ".gitignore",
    "requirements.txt",
    "playlists.json",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
    "DEPLOY_VPS.md",
    "music-api.service",
    "music-api-user.service",
    "nginx.conf.example",
    "music/.gitkeep",
    "logs/.gitkeep",
    "cache/.gitkeep",
    "cache/stats.json",
]

exclude_names = {
    "__pycache__",
    ".git",
    ".venv",
    ".pytest_cache",
    ".DS_Store",
    "Thumbs.db",
    "ytdlp",
}

exclude_exts = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".zip",
    ".log",
    ".part",
    ".ytdl",
    ".mp3",
    ".m4a",
    ".mp4",
    ".opus",
    ".webm",
    ".wav",
    ".flac",
}

added_entries = []

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for item_rel in include_items:
        p = root_dir / item_rel
        if not p.exists():
            continue

        if p.is_file():
            arcname = Path(item_rel).as_posix()
            zf.write(p, arcname=arcname)
            added_entries.append(arcname)
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in exclude_names and not d.startswith(".")]
                for file in files:
                    if any(file.endswith(ext) for ext in exclude_exts):
                        continue
                    if file.startswith(".") and file != ".gitkeep":
                        continue
                    file_path = Path(root) / file
                    rel_path = file_path.relative_to(root_dir)
                    arcname = rel_path.as_posix()
                    zf.write(file_path, arcname=arcname)
                    added_entries.append(arcname)

print(f"Total entries packaged: {len(added_entries)}")
print(f"Zip path: {zip_path.name} ({zip_path.stat().st_size / 1024:.1f} KB)\n")

# Verify all contents
with zipfile.ZipFile(zip_path, "r") as zf:
    names = zf.namelist()
    print("Files inside fasttube_deploy.zip:")
    for n in sorted(names):
        print("  [+]", n)

    assert any("\\" in n for n in names) is False, "Error: Backslash found in zip paths!"
    assert any("__pycache__" in n for n in names) is False, "Error: __pycache__ found in zip!"
    assert "app.py" in names, "Error: app.py missing from zip!"
    assert ".env" in names, "Error: .env missing from zip!"
    assert "app/main.py" in names, "Error: app/main.py missing from zip!"
    assert "app/static/index.html" in names, "Error: app/static/index.html missing from zip!"
    assert "cookies/www.youtube.com_cookies.txt" in names, "Error: cookies/www.youtube.com_cookies.txt missing from zip!"
    assert "scripts/start.sh" in names, "Error: scripts/start.sh missing from zip!"
    assert "scripts/build.sh" in names, "Error: scripts/build.sh missing from zip!"
    print("\nVERIFICATION PASSED: fasttube_deploy.zip contains all 100% required production files with POSIX paths.")
