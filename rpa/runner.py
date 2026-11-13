"""Orchestrates the form-filling run against the mock portal."""

import json
import sys
from pathlib import Path

from .form import find_form, find_input, first_error, hidden_value, parse_page
from .rules import (
    AMOUNT_RANGE,
    DUPLICATE,
    REMARK_TOO_LONG,
    classify_error,
    fix_amount,
    fix_remark,
)
from .session import PortalSession
from .store import Store

MAX_ROUNDS = 5
MAX_CSRF_RETRIES = 3


def load_portal(site):
    """Start the mock portal described by the site directory."""
    from mock_portal.server import PortalServer

    config = json.loads((Path(site) / "portal.json").read_text(encoding="utf-8"))
    return PortalServer(config).start(), config


def login(session, users):
    if not users:
        raise RuntimeError("站点配置里没有账号 (portal.json: users)")
    username, password = next(iter(users.items()))
    status, body = session.post(
        "/login", {"username": username, "password": password}
    )
    if status != 200 or not session.has_session_cookie():
        raise RuntimeError(f"登录失败 (HTTP {status})")


def post_form(session, path, fields):
    """POST a form, refreshing the CSRF token from the 403 page if needed."""
    status, body = 0, ""
    for _ in range(MAX_CSRF_RETRIES):
        status, body = session.post(path, fields)
        if status != 403:
            return status, body
        page = parse_page(body)
        form = find_form(page, path)
        token = hidden_value(form, "csrf_token") if form else None
        if not token:
            return status, body
        fields = dict(fields, csrf_token=token)
    return status, body


def _amount_bounds(form):
    field = find_input(form, "amount")
    if not field:
        return None, None
    low = float(field["min"]) if field["min"] not in (None, "") else None
    high = float(field["max"]) if field["max"] not in (None, "") else None
    return low, high


def _remark_limit(form):
    field = find_input(form, "remark")
    if field and field["maxlength"]:
        return int(field["maxlength"])
    return None


def process_record(session, record):
    """Run one record through the two-step form.

    Returns (status, attempts, last_error, final_data). Each attempt is one
    full pass through the form; corrections derived from field errors are
    applied on the next attempt.
    """
    data = {
        "order_no": str(record["order_no"]),
        "amount": record["amount"],
        "remark": record.get("remark", ""),
    }
    attempts = 0
    last_error = "none"
    while attempts < MAX_ROUNDS:
        attempts += 1
        status, body = session.get("/form/step1")
        if status != 200:
            last_error = f"无法打开第一步表单 (HTTP {status})"
            return "failed", attempts, last_error, data
        page = parse_page(body)
        token = hidden_value(find_form(page, "/form/step1"), "csrf_token")
        status, body = post_form(
            session,
            "/form/step1",
            {
                "csrf_token": token,
                "order_no": data["order_no"],
                "amount": str(data["amount"]),
            },
        )
        page = parse_page(body)
        error = first_error(page)
        if error:
            last_error = error["message"].strip() or last_error
            kind = classify_error(error["message"])
            if kind == DUPLICATE:
                return "skipped", attempts, last_error, data
            if kind == AMOUNT_RANGE:
                low, high = _amount_bounds(find_form(page, "/form/step1"))
                data["amount"] = fix_amount(data["amount"], low, high)
                continue
            return "failed", attempts, last_error, data

        # step1 accepted: the 303 redirect landed us on the step2 form
        form2 = find_form(page, "/form/step2")
        if form2 is None:
            last_error = "第一步提交后未到达第二步表单"
            return "failed", attempts, last_error, data
        token = hidden_value(form2, "csrf_token")
        status, body = post_form(
            session,
            "/form/step2",
            {"csrf_token": token, "remark": data["remark"]},
        )
        page = parse_page(body)
        error = first_error(page)
        if error:
            last_error = error["message"].strip() or last_error
            kind = classify_error(error["message"])
            if kind == REMARK_TOO_LONG:
                limit = _remark_limit(find_form(page, "/form/step2"))
                if limit is None:
                    return "failed", attempts, last_error, data
                data["remark"] = fix_remark(data["remark"], limit)
                continue
            if kind == DUPLICATE:
                return "skipped", attempts, last_error, data
            return "failed", attempts, last_error, data
        return "success", attempts, last_error, data
    return "failed", attempts, last_error, data


def run(site, records_path, db_path, stdout=None):
    out = stdout if stdout is not None else sys.stdout
    server, config = load_portal(site)
    try:
        session = PortalSession(server.url)
        login(session, config.get("users", {}))
        store = Store(db_path)
        try:
            records = [
                json.loads(line)
                for line in Path(records_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            done = store.completed()
            submitted = 0
            for record in records:
                order_no = str(record["order_no"])
                # idempotent: already-completed order numbers (including a
                # record listed twice in the same file) are never resubmitted
                if order_no in done:
                    continue
                status, attempts, last_error, final = process_record(session, record)
                store.save_outcome(order_no, status, attempts, last_error, final)
                done.add(order_no)
                submitted += 1
            tally = store.tally([str(r["order_no"]) for r in records])
        finally:
            store.close()
        summary = {
            "submitted": submitted,
            "success": tally["success"],
            "skipped": tally["skipped"],
            "failed": tally["failed"],
            "requests": session.requests,
        }
        print(
            "summary: submitted={submitted} success={success} skipped={skipped}"
            " failed={failed} requests={requests}".format(**summary),
            file=out,
        )
        return summary
    finally:
        server.stop()
