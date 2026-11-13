"""CLI entry: python -m rpa.formfill --site ... --records ... --db ..."""

import argparse

from . import runner


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rpa.formfill")
    parser.add_argument("--site", required=True, help="站点目录 (含 portal.json)")
    parser.add_argument("--records", required=True, help="待录入清单 (JSONL)")
    parser.add_argument("--db", required=True, help="本地 sqlite 数据库路径")
    args = parser.parse_args(argv)
    runner.run(args.site, args.records, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

