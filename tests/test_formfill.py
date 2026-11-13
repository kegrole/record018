"""End-to-end tests for rpa.formfill against the mock portal.

Every test builds its own portal config + record list with randomized
values (same rules, different concrete data) to prove nothing is
hard-coded to the demo fixture.
"""

import io
import json
import random
import sqlite3
import string
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_portal.server import PortalServer
from rpa import runner
from rpa.store import Store


def make_fixture(tmp, seed=42, request_delay_ms=0):
    """Build a randomized site dir + records list following the brief rules."""
    rnd = random.Random(seed)
    tag = "".join(rnd.choices(string.ascii_uppercase + string.digits, k=8))
    amount_max = rnd.choice([5000, 10000, 20000])
    remark_max = rnd.choice([40, 50, 64])
    seed_order = f"{tag}-SEED-1"

    site = Path(tmp) / "site"
    site.mkdir(parents=True)
    config = {
        "users": {"operator": "pw-" + tag},
        "amount_min": 0.01,
        "amount_max": amount_max,
        "remark_max": remark_max,
        "request_delay_ms": request_delay_ms,
        "seed_records": [
            {"order_no": seed_order, "amount": 1.0, "remark": "portal seed"}
        ],
    }
    (site / "portal.json").write_text(json.dumps(config), encoding="utf-8")

    dup_order = seed_order
    amount_order = f"{tag}-A10"
    remark_order = f"{tag}-A11"
    # 12 lines total: 8 distinct normals + 1 normal listed twice (idempotency)
    # + 1 server-side duplicate + 1 out-of-range amount + 1 over-long remark
    normals = [
        {"order_no": f"{tag}-A{i:02d}", "amount": round(rnd.uniform(1, amount_max), 2),
         "remark": f"normal {i}"}
        for i in range(1, 9)
    ]
    records = list(normals)
    records.append(dict(normals[-1]))
    records.append({"order_no": dup_order, "amount": 5.0, "remark": "dup"})
    records.append({"order_no": amount_order, "amount": amount_max * 3, "remark": "too big"})
    records.append({"order_no": remark_order, "amount": 6.0, "remark": "R" * (remark_max + 30)})
    rnd.shuffle(records)

    records_path = Path(tmp) / "records.jsonl"
    records_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return {
        "site": str(site),
        "records": str(records_path),
        "db": str(Path(tmp) / "out" / "rpa.db"),
        "amount_max": amount_max,
        "remark_max": remark_max,
        "dup_order": dup_order,
        "amount_order": amount_order,
        "remark_order": remark_order,
        "orders": [r["order_no"] for r in records],
    }


def fetch(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


class FullRunTest(unittest.TestCase):
    """判定 1-5 与 8: full run outcomes, idempotency, schema."""

    def test_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = make_fixture(tmp, seed=1)
            out = io.StringIO()
            summary = runner.run(fx["site"], fx["records"], fx["db"], stdout=out)

            # summary line printed to stdout
            line = out.getvalue().strip()
            self.assertTrue(line.startswith("summary: "), line)
            self.assertIn("success=10", line)
            self.assertIn("skipped=1", line)
            self.assertIn("failed=0", line)
            self.assertIn(f"requests={summary['requests']}", line)
            self.assertEqual(summary["submitted"], 11)

            # 判定 2: outcomes 10 success / 1 skipped / 0 failed
            rows = fetch(fx["db"], "SELECT record_id, status, attempts, last_error FROM outcomes")
            self.assertEqual(len(rows), 11)
            by_id = {r[0]: {"status": r[1], "attempts": r[2], "last_error": r[3]} for r in rows}
            statuses = [r["status"] for r in by_id.values()]
            self.assertEqual(statuses.count("success"), 10)
            self.assertEqual(statuses.count("skipped"), 1)
            self.assertEqual(statuses.count("failed"), 0)

            # 判定 2: duplicate lands on skipped with exactly 1 attempt
            dup = by_id[fx["dup_order"]]
            self.assertEqual(dup["status"], "skipped")
            self.assertEqual(dup["attempts"], 1)
            self.assertIn("单号重复", dup["last_error"])

            # 判定 3: out-of-range amount fixed and succeeded, attempts >= 2
            amt = by_id[fx["amount_order"]]
            self.assertEqual(amt["status"], "success")
            self.assertGreaterEqual(amt["attempts"], 2)
            self.assertIn("金额超出范围", amt["last_error"])
            stored = fetch(fx["db"], "SELECT amount FROM records WHERE order_no=?",
                           (fx["amount_order"],))
            self.assertEqual(stored[0][0], fx["amount_max"])

            # 判定 4: over-long remark truncated to the portal limit
            rmk = by_id[fx["remark_order"]]
            self.assertEqual(rmk["status"], "success")
            self.assertIn("备注过长", rmk["last_error"])
            stored = fetch(fx["db"], "SELECT remark FROM records WHERE order_no=?",
                           (fx["remark_order"],))
            self.assertEqual(len(stored[0][0]), fx["remark_max"])

            # 判定 5: order_no unique, row count == success + skipped
            rec_rows = fetch(fx["db"], "SELECT order_no FROM records")
            self.assertEqual(len(rec_rows), 11)
            self.assertEqual(len({r[0] for r in rec_rows}), 11)

            # 判定 8: outcomes schema + non-empty last_error
            cols = [r[1] for r in fetch(fx["db"], "PRAGMA table_info(outcomes)")]
            for col in ("record_id", "status", "attempts", "last_error"):
                self.assertIn(col, cols)
            for row in by_id.values():
                self.assertTrue(row["last_error"])

            # 判定 5: rerun must not add rows nor resubmit
            out2 = io.StringIO()
            summary2 = runner.run(fx["site"], fx["records"], fx["db"], stdout=out2)
            self.assertEqual(summary2["submitted"], 0)
            self.assertEqual(fetch(fx["db"], "SELECT COUNT(*) FROM records")[0][0], 11)
            self.assertEqual(fetch(fx["db"], "SELECT COUNT(*) FROM outcomes")[0][0], 11)


class ResumeTest(unittest.TestCase):
    """判定 6: kill mid-run, restart, still exactly 11 unique records."""

    def test_resume_after_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = make_fixture(tmp, seed=7, request_delay_ms=60)
            env = dict(**__import__("os").environ, PYTHONPATH=str(ROOT))
            cmd = [
                sys.executable, "-m", "rpa.formfill",
                "--site", fx["site"],
                "--records", fx["records"],
                "--db", fx["db"],
            ]
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            # wait until the run is genuinely in flight, then kill it
            deadline = time.time() + 30
            done = 0
            while time.time() < deadline:
                try:
                    done = fetch(fx["db"], "SELECT COUNT(*) FROM outcomes")[0][0]
                except sqlite3.OperationalError:
                    done = 0
                if done >= 2:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(done, 2, "runner did not make progress")
            proc.kill()
            proc.communicate(timeout=10)
            self.assertLess(fetch(fx["db"], "SELECT COUNT(*) FROM outcomes")[0][0], 11)

            # restart from the checkpoint and finish
            result = subprocess.run(
                cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("success=10", result.stdout)
            self.assertIn("skipped=1", result.stdout)
            self.assertIn("failed=0", result.stdout)

            rec_rows = fetch(fx["db"], "SELECT order_no FROM records")
            self.assertEqual(len(rec_rows), 11)
            self.assertEqual(len({r[0] for r in rec_rows}), 11)


class PortalSecurityTest(unittest.TestCase):
    """判定 7: the portal really enforces session cookie and CSRF token."""

    CONFIG = {
        "users": {"u": "p"},
        "amount_min": 0.01,
        "amount_max": 100,
        "remark_max": 10,
        "seed_records": [],
    }

    def test_session_and_csrf_enforced(self):
        with PortalServer(self.CONFIG) as server:
            # no cookie -> rejected
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(server.url + "/form/step1")
            self.assertEqual(ctx.exception.code, 403)

            # login to obtain a session cookie
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor()
            )
            body = "username=u&password=p".encode()
            resp = opener.open(urllib.request.Request(server.url + "/login", data=body))
            self.assertEqual(resp.status, 200)

            # wrong CSRF token -> rejected
            bad = "csrf_token=not-a-real-token&order_no=X-1&amount=5".encode()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                opener.open(urllib.request.Request(server.url + "/form/step1", data=bad))
            self.assertEqual(ctx.exception.code, 403)

            # valid token from the form page -> accepted
            from rpa.form import find_form, hidden_value, parse_page

            page = parse_page(opener.open(server.url + "/form/step1").read().decode())
            token = hidden_value(find_form(page, "/form/step1"), "csrf_token")
            good = f"csrf_token={token}&order_no=X-1&amount=5".encode()
            resp = opener.open(urllib.request.Request(server.url + "/form/step1", data=good))
            self.assertEqual(resp.status, 200)
            self.assertIn("/form/step2", resp.geturl())


if __name__ == "__main__":
    unittest.main()
