import argparse
import re
from pathlib import Path


SEASON_RE = re.compile(r"Season\s+(\d+)", re.IGNORECASE)
EPISODE_RE = re.compile(r"Episode\s+(\d+)", re.IGNORECASE)


def rename_and_move_subtitles(root: Path, show_name: str, target_dir: Path, apply: bool):
    target_dir.mkdir(parents=True, exist_ok=True)

    for srt in root.rglob("*.srt"):
        season = None
        episode = None

        # Walk up folder tree to find Season / Episode
        for parent in srt.parents:
            if season is None:
                m = SEASON_RE.search(parent.name)
                if m:
                    season = int(m.group(1))

            if episode is None:
                m = EPISODE_RE.search(parent.name)
                if m:
                    episode = int(m.group(1))

        if season is None or episode is None:
            print(f"[SKIP] Could not determine S/E for: {srt}")
            continue

        new_name = f"{show_name} S{season:02d}E{episode:02d}.srt"
        new_path = target_dir / new_name

        if apply:
            if new_path.exists():
                print(f"[SKIP] Target already exists: {new_path.name}")
                continue

            print(f"[MOVE] {srt} -> {new_path}")
            srt.rename(new_path)
        else:
            print(f"[DRY RUN] Would move: {srt} -> {new_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Rename and move subtitle files based on Season/Episode folders"
    )
    parser.add_argument(
        "root",
        type=Path,
        help="Root folder of extracted OpenSubtitles files",
    )
    parser.add_argument(
        "show_name",
        help="Show name (e.g. 'Star Wars The Clone Wars')",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(r"C:\Users\thunderhead\Downloads"),
        help="Target folder for renamed .srt files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rename and move files",
    )

    args = parser.parse_args()

    rename_and_move_subtitles(
        root=args.root,
        show_name=args.show_name,
        target_dir=args.target,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
