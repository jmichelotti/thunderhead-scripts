import os
from pathlib import Path

ROOT = Path("C:/")

def folder_size(path: Path, max_depth=3) -> int:
    total = 0
    base_depth = len(path.parts)

    for root, dirs, files in os.walk(path, topdown=True):
        depth = len(Path(root).parts) - base_depth
        if depth > max_depth:
            dirs[:] = []
            continue

        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except (PermissionError, FileNotFoundError):
                pass

    return total

def main():
    results = []

    for item in ROOT.iterdir():
        if not item.is_dir():
            continue

        print(f"Scanning {item}...")
        size = folder_size(item)

        results.append((item, size))

    results.sort(key=lambda x: x[1], reverse=True)

    print("\nTop folders by size:\n")
    for folder, size in results[:20]:
        gb = size / (1024 ** 3)
        print(f"{gb:8.2f} GB  {folder}")


if __name__ == "__main__":
    main()
