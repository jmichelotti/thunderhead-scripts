import subprocess
import sys

ROBOCOPY = "robocopy"

JOBS = [
    (r"F:\TV Shows\Australian Survivor (2016)",
     r"L:\TV Shows\Australian Survivor (2016)"),
]

#     ("E:\\TV Shows", "F:\\TV Shows"),
#     ("E:\\Movies", "F:\\Movies"),
# ]

ROBOCOPY_FLAGS = [
    "/E",
    "/COPY:DAT",
    "/DCOPY:T",
    "/Z",
    "/R:1",
    "/W:1",
    "/MT:8",
    "/ETA",
    "/NP",
]

def run_robocopy(src, dst):
    cmd = [ROBOCOPY, src, dst] + ROBOCOPY_FLAGS

    print(f"\n=== Copying ===")
    print(f"FROM: {src}")
    print(f"TO  : {dst}")
    print("=" * 50)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream robocopy output live
    for line in process.stdout:
        print(line, end="")

    process.wait()

    # Robocopy exit codes:
    # 0–7 = success (with various conditions)
    if process.returncode > 7:
        print(f"\n❌ Robocopy failed with exit code {process.returncode}")
        sys.exit(process.returncode)

    print(f"\n✅ Finished copying {src}\n")


def main():
    for src, dst in JOBS:
        run_robocopy(src, dst)

    print("\n🎉 ALL TRANSFERS COMPLETE 🎉")


if __name__ == "__main__":
    main()
