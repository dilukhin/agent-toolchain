#!/usr/bin/env python3
"""Publish one validated price document using Git Data API and non-force ref updates."""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.routerai_prices import (BRANCH, FILE, MAX_BYTES, PUBLISHER, PriceError,
                                        approve, verify, timestamp)
except ModuleNotFoundError:
    from routerai_prices import (BRANCH, FILE, MAX_BYTES, PUBLISHER, PriceError,
                                approve, verify, timestamp)


class Missing(PriceError):
    pass


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(
            f"https://api.github.com/repos/{PUBLISHER}/{path}", data=raw, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_BYTES * 2 + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                raise Missing("GitHub object missing") from None
            raise PriceError(f"GitHub {method} failed (HTTP {exc.code})") from None
        except (OSError, urllib.error.URLError):
            raise PriceError("GitHub transport failure") from None
        if len(data) > MAX_BYTES * 2:
            raise PriceError("GitHub response oversized")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise PriceError("invalid GitHub response") from None


def _head(api, ref: str) -> str:
    return api.call("GET", f"git/ref/heads/{ref}")["object"]["sha"]


def previous_document(api):
    try:
        head = _head(api, BRANCH)
    except Missing:
        return None, None
    commit = api.call("GET", f"git/commits/{head}")
    tree = api.call("GET", f"git/trees/{commit['tree']['sha']}?recursive=1")
    entries = tree.get("tree", [])
    if tree.get("truncated") or len(entries) != 1 or entries[0].get("path") != FILE or entries[0].get("mode") != "100644" or entries[0].get("type") != "blob":
        raise PriceError("price branch tree ownership mismatch")
    blob = api.call("GET", f"git/blobs/{entries[0]['sha']}")
    if blob.get("encoding") != "base64" or blob.get("size", MAX_BYTES + 1) > MAX_BYTES:
        raise PriceError("invalid published blob")
    try:
        raw = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        value = json.loads(raw)
    except (KeyError, ValueError, UnicodeError):
        raise PriceError("corrupt published document") from None
    return head, verify(value, published=True)


def validation_evidence(api, sha: str, run_id: int):
    workflow = api.call("GET", "actions/workflows/validate.yml")
    run = api.call("GET", f"actions/runs/{run_id}")
    if run.get("workflow_id") != workflow["id"] or run.get("head_sha") != sha or run.get("status") != "completed" or run.get("conclusion") != "success":
        raise PriceError("full validation does not match candidate SHA")
    jobs = []
    page = 1
    while True:
        value = api.call("GET", f"actions/runs/{run_id}/jobs?per_page=100&page={page}")
        jobs.extend(value.get("jobs", []))
        if len(jobs) >= value["total_count"]:
            break
        page += 1
        if page > 10:
            raise PriceError("too many validation jobs")
    for name in ("windows", "linux"):
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise PriceError("Windows/Linux validation incomplete")


def publish(api, candidate: dict, *, sha: str, run_id: int, at: str):
    verify(candidate, published=False)
    checked = timestamp(candidate["source_checked_at"])
    if not 0 <= (timestamp(at) - checked).total_seconds() <= 86400:
        raise PriceError("source observation too old or future")
    validation_evidence(api, sha, run_id)
    if _head(api, "automation/routerai-catalog") != sha:
        raise PriceError("candidate moved during validation")
    old_head, previous = previous_document(api)
    if previous and previous["source_checked_at"] == candidate["source_checked_at"] and previous["snapshot_id"] == candidate["snapshot_id"] and previous["validation"]["candidate_sha"] == sha and previous["validation"]["run_id"] == run_id:
        return old_head, previous  # Retry after a successful ref write / lost read-back.
    document = approve(candidate, previous, candidate_sha=sha, run_id=run_id, at=at)
    raw = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise PriceError("export oversized")
    blob = api.call("POST", "git/blobs", {"content": raw, "encoding": "utf-8"})
    tree = api.call("POST", "git/trees", {"tree": [{"path": FILE, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
    commit = api.call("POST", "git/commits", {"message": "Publish checked RouterAI prices", "tree": tree["sha"], "parents": [old_head] if old_head else []})
    # Recheck both leases immediately before the only mutable operation. Competing
    # sibling commits cannot fast-forward one another; never force an update.
    if _head(api, "automation/routerai-catalog") != sha:
        raise PriceError("candidate moved before publication")
    if old_head is None:
        try:
            _head(api, BRANCH)
        except Missing:
            pass
        else:
            raise PriceError("price branch appeared concurrently")
        api.call("POST", "git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    else:
        if _head(api, BRANCH) != old_head:
            raise PriceError("price branch changed concurrently")
        api.call("PATCH", f"git/refs/heads/{BRANCH}", {"sha": commit["sha"], "force": False})
    head, actual = previous_document(api)
    if head != commit["sha"] or actual != document:
        raise PriceError("publication read-back mismatch")
    return head, document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--validation-run", type=int, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("GITHUB_REPOSITORY") != PUBLISHER or os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise PriceError("publisher must run from the repository main workflow")
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise PriceError("missing GitHub credential")
        raw = args.candidate.read_bytes()
        if len(raw) > MAX_BYTES:
            raise PriceError("candidate oversized")
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        head, document = publish(GitHub(token), json.loads(raw), sha=args.candidate_sha, run_id=args.validation_run, at=at)
    except (PriceError, OSError, ValueError, KeyError, TypeError):
        # Do not publish raw HTTP bodies, credentials or untrusted exception text.
        print("RouterAI price publication refused; last-known-good preserved.")
        return 2
    print(f"RouterAI prices published: {document['snapshot_id']} at commit {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
