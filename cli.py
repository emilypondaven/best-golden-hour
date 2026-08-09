"""First Light - CLI entry point.

Run:
    python cli.py 51.42 -0.27          # coordinates are required
    python cli.py 51.42 -0.27 --force  # re-score even if cached today
"""

import sys

from storage import load_or_create


def show(report: dict) -> None:
    where = f"{report['lat']}, {report['lon']}"
    print(f"\n  {where}")
    print(f"  {report['week_summary']}\n")
    for d in sorted(report["days"], key=lambda x: x["score"], reverse=True):
        star = "*" if d["date"] == report["best_date"] else " "
        print(f"  {star} {d['weekday']:<10} {d['sunrise']['time']}  {d['score']:>3}  {d['reason']}")
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit("Usage: python cli.py LAT LON   e.g. python cli.py 51.42 -0.27")
    show(load_or_create(float(args[0]), float(args[1]), force="--force" in sys.argv))
