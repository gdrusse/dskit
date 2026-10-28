"""Opt-in, pinned 2019 MIO one-day account mark coverage.

Preparation and execution are explicit stages. Importing this module reads nothing.
Original market evidence is immutable; every derived file lives below output_root.
"""

from collections import defaultdict
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re

from dskit.pipeline.document import load_document
from dskit.pipeline.driver import RunAttestation, resolve_json_artifact, run_document
from dskit.pipeline.option_pricing import OptionScenarioReprice

ENVELOPE = "standardized moneyness is outside numeric envelope [-3, 3]"
LEG_ID = re.compile(r"leg '([^']+)'|(?:_leg_)(\d+)|legs\[(\d+)\]")


def sha256(path):
    """Hash bytes without changing the input."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text())


def _write_json(path, value, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text() == payload:
            return
        if not replace:
            raise ValueError(f"existing output differs: {path}")
    path.write_text(payload)


def _rows(path):
    with Path(path).open(newline="") as stream:
        return list(csv.DictReader(stream))


def select_numeric_jobs(jobs):
    """Choose only explicit, otherwise clean, legacy envelope refusals."""
    keys = [job["key"] for job in jobs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate task-index job key")
    return [job for job in jobs if job["preflight_refusals"]
            and all(ENVELOPE in reason for reason in job["preflight_refusals"])]


def _causes(reason):
    reasons = [part.strip() for part in reason.split(";") if part.strip()]
    causes = []
    for part in reasons:
        if ENVELOPE in part:
            cause = "numeric_envelope"
        elif "outside stable-tail numeric range" in part:
            cause = "numeric_stable_range"
        elif "quote_iv" in part or (".iv must be positive" in part):
            cause = "source_iv_unavailable"
        elif "basis" in part:
            cause = "source_basis_unavailable"
        else:
            cause = "other_refusal"
        match = LEG_ID.search(part)
        causes.append({"cause": cause, "leg_id": (
            match.group(1) if match.group(1) else "leg_" + (match.group(2) or match.group(3))
        ) if match else None, "reason": part,
                       "phase": "baseline" if part.startswith("baseline") else
                       "scenario" if part.startswith("scenarios[") else "input"})
    return causes


def _ratio(numerator, denominator):
    if denominator <= 0:
        return {"value": None, "reason": "nonpositive_denominator"}
    return {"value": numerator / denominator, "reason": None}


def aggregate_weekly(daily, scenario, rows):
    """Join every held book once to the observed account basis."""
    expected = json.loads(daily["open_ids"])
    if len(set(expected)) != len(expected):
        raise ValueError("duplicate daily open_ids")
    selected = [row for row in rows if row["cohort"] == daily["cohort"]
                and row["method"] == daily["method"] and row["date"] == daily["date"]
                and row["scenario"] == scenario and row["kind"] == "weekly_open_book"]
    actual = [row["group_id"] for row in selected]
    if len(set(actual)) != len(actual):
        raise ValueError("duplicate weekly group_id")
    if sorted(actual) != sorted(expected):
        raise ValueError("weekly books do not exactly match daily open_ids")
    cash = float(daily["cash"])
    funding = float(daily["funding"])
    contributions = float(daily["contributions"])
    result = {
        "cohort": daily["cohort"], "method": daily["method"],
        "date": daily["date"], "scenario": scenario, "cash_usd": cash,
        "funding_usd": funding, "contributions_usd": contributions,
        "observed_nbbo_equity_usd": float(daily["equity"]),
        "held_books": len(expected), "priced_books": sum(r["status"] == "priced" for r in selected),
        "book_contributions": [], "unpriced_books": [], "refusal_causes": [],
        "model_baseline_equity_usd": None, "hypothetical_scenario_equity_usd": None,
        "hypothetical_model_equity_change_usd": None, "loss_usd": None,
        "loss_over_funding": None, "loss_over_model_baseline_equity": None,
        "loss_over_contributions": None, "ratio_null_reasons": {},
    }
    for row in sorted(selected, key=lambda r: r["group_id"]):
        lineage = row.get("leg_lineage", [])
        if row["status"] == "priced":
            base = float(row["model_base_value_usd"])
            shocked = float(row["scenario_full_value_usd"])
            result["book_contributions"].append({
                "group_id": row["group_id"], "key": row.get("key"),
                "model_base_value_usd": base, "scenario_value_usd": shocked,
                "mark_change_usd": shocked - base, "leg_lineage": lineage,
                "report_sha256": row.get("report_sha256"),
            })
        else:
            causes = _causes(row["reason"])
            result["unpriced_books"].append({
                "group_id": row["group_id"], "key": row.get("key"),
                "causes": causes, "leg_lineage": lineage})
            result["refusal_causes"].extend(c["cause"] for c in causes)
    result["refusal_causes"] = sorted(set(result["refusal_causes"]))
    if not expected:
        result["status"] = "cash_only"
    elif scenario.startswith("expiry_"):
        result["status"] = "per_book_different_expiries"
    elif result["unpriced_books"]:
        result["status"] = "incomplete"
    else:
        result["status"] = "complete"
        base = math.fsum([cash] + [b["model_base_value_usd"] for b in result["book_contributions"]])
        shocked = math.fsum([cash] + [b["scenario_value_usd"] for b in result["book_contributions"]])
        change = math.fsum(b["mark_change_usd"] for b in result["book_contributions"])
        loss = max(0.0, -change)
        if scenario == "unchanged" and abs(change) > 1e-7:
            raise ValueError("zero-shock account mark changed")
        result.update(model_baseline_equity_usd=base,
                      hypothetical_scenario_equity_usd=shocked,
                      hypothetical_model_equity_change_usd=change, loss_usd=loss)
        for key, denominator in (
                ("loss_over_funding", funding),
                ("loss_over_model_baseline_equity", base),
                ("loss_over_contributions", contributions)):
            ratio = _ratio(loss, denominator)
            result[key] = ratio["value"]
            if ratio["reason"]:
                result["ratio_null_reasons"][key] = ratio["reason"]
    return result


class CoverageDriver:
    """Pinned input verification, distinct opt-in attempts, account rebuild."""

    def __init__(self, output_root, original_root, pin_path, pin_sha256,
                 expected_envelope, expected_eligible):
        self.output_root = Path(output_root).resolve()
        self.original_root = Path(original_root).resolve()
        self.pin_path = Path(pin_path).resolve()
        self.pin_sha256 = pin_sha256
        self.expected_envelope = expected_envelope
        self.expected_eligible = expected_eligible
        if not (isinstance(expected_envelope, int) and isinstance(expected_eligible, int)
                and expected_envelope > 0 and 0 <= expected_eligible <= expected_envelope):
            raise ValueError("invalid expected numeric counts")
        if self.output_root == self.original_root or self.original_root in self.output_root.parents and self.output_root.name in {"limited36", "broad212"}:
            raise ValueError("output_root must be distinct from original cohort")
        if self.output_root.parent != self.original_root:
            raise ValueError("output_root must be a new directory below the original root")

    def _artifact_index(self):
        if sha256(self.pin_path) != self.pin_sha256:
            raise ValueError("original results pin SHA-256 mismatch")
        pins = _read_json(self.pin_path)
        if pins["status"] != "completed_development_evidence":
            raise ValueError("original packet is not complete")
        artifact_pin = pins["evidence"]["artifact-index.json"]
        artifact_path = Path(artifact_pin["path"]).resolve()
        if artifact_path != self.original_root / "artifact-index.json":
            raise ValueError("artifact-index path differs from original root")
        if sha256(artifact_path) != artifact_pin["sha256"]:
            raise ValueError("original artifact-index SHA-256 mismatch")
        return pins, _read_json(artifact_path)

    def _input_hashes(self, cohort):
        pins, artifact = self._artifact_index()
        if cohort not in ("limited36", "broad212"):
            raise ValueError("unsupported cohort")
        hashes = {"stress/task-index.json":
                  pins["evidence"][cohort + "/stress/task-index.json"]["sha256"]}
        for name in ("stress-books.csv", "stress-weekly-account.csv", "daily-account.csv"):
            hashes[name] = artifact["csv_files"][cohort + "/" + name]["sha256"]
        return hashes

    @staticmethod
    def _write_csv(path, rows):
        if not rows:
            raise ValueError("cannot write empty coverage CSV")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})
        payload = stream.getvalue()
        if path.exists():
            if path.read_text() != payload:
                raise ValueError(f"existing output differs: {path}")
            return
        path.write_text(payload)

    def _derived_document(self, cohort, job):
        original = _read_json(job["config"])
        node = original["pipeline"]["stress"]
        if node["uses"] != "dskit.pipeline.option_pricing:OptionScenarioReprice":
            raise ValueError("original stress class mismatch")
        expected = json.loads(json.dumps(original))
        expected["pipeline"]["stress"]["params"]["numeric_policy"] = "stable_tail_v1"
        expected["name"] += "-stable-tail-v1"
        expected["notes"] += " Opt-in stable_tail_v1 numeric coverage; original refusal retained."
        expected["outputs"]["run_root"] = str(self.output_root / cohort / "runs")
        issues = OptionScenarioReprice.validate_params(expected["pipeline"]["stress"]["params"])
        return expected, issues

    def _validate_selection(self, cohort, index, selection):
        jobs = {job["key"]: job for job in select_numeric_jobs(index["jobs"])}
        if set(jobs) != {item["key"] for item in selection["jobs"]}:
            raise ValueError("selection differs from pinned original refusal set")
        refused = []
        for item in selection["jobs"]:
            job = jobs[item["key"]]
            expected, issues = self._derived_document(cohort, job)
            if item["original_config_sha256"] != job["config_sha256"]:
                raise ValueError("selection original config SHA-256 mismatch")
            if issues:
                refused.append(item["key"])
                if item != {"key": job["key"], "status": "opt_in_refused",
                            "reasons": issues, "original_config_sha256": job["config_sha256"]}:
                    raise ValueError("selection opt-in refusal differs from preflight")
            else:
                expected_keys = {"key", "status", "config", "config_sha256",
                                 "original_config_sha256", "date", "scenario"}
                if set(item) != expected_keys or item["status"] != "eligible":
                    raise ValueError("selection eligible fields mismatch")
                if item["date"] != job["date"] or item["scenario"] != job["scenario"]:
                    raise ValueError("selection date/scenario mismatch")
                path = self.output_root / cohort / "configs" / (job["key"] + "-stable-tail-v1.json")
                if Path(item["config"]).resolve() != path or sha256(path) != item["config_sha256"]:
                    raise ValueError("selection opt-in config SHA-256 mismatch")
                if _read_json(path) != expected:
                    raise ValueError("selection opt-in config differs from pinned original")
        if len(refused) != self.expected_envelope - self.expected_eligible:
            raise ValueError("opt-in refusal count differs from expected numeric coverage")

    def _selection(self, cohort):
        selection = _read_json(self.output_root / cohort / "selection.json")
        if (selection["cohort"] != cohort or selection["input_sha256"] != self._input_hashes(cohort)
                or selection["original_results_pin_sha256"] != self.pin_sha256
                or selection["schema_version"] != 1):
            raise ValueError("selection pin mismatch")
        jobs = selection["jobs"]
        keys = [item["key"] for item in jobs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate selection job key")
        if len(jobs) != self.expected_envelope or sum(
                item["status"] == "eligible" for item in jobs) != self.expected_eligible:
            raise ValueError("selection coverage count mismatch")
        return selection

    def originals(self, cohort):
        """Verify the exact approved 2019 packet before interpretation."""
        input_hashes = self._input_hashes(cohort)
        root = self.original_root / cohort
        for name, expected in input_hashes.items():
            if sha256(root / name) != expected:
                raise ValueError(f"original SHA-256 mismatch: {cohort}/{name}")
        index = _read_json(root / "stress/task-index.json")
        if index["cohort"] != cohort:
            raise ValueError("task-index cohort mismatch")
        jobs = index["jobs"]
        if len({job["key"] for job in jobs}) != len(jobs):
            raise ValueError("duplicate task-index job key")
        books = _rows(root / "stress-books.csv")
        if len(jobs) != len(books):
            raise ValueError("task-index/book count mismatch")
        for job, row in zip(jobs, books):
            for field in ("method", "kind", "date", "ticker", "expiry", "group_id", "scenario", "config", "config_sha256"):
                if str(job[field]) != row[field]:
                    raise ValueError("task-index/book row-key mismatch")
            if sha256(job["config"]) != job["config_sha256"]:
                raise ValueError("original config SHA-256 mismatch")
        weekly = _rows(root / "stress-weekly-account.csv")
        daily = _rows(root / "daily-account.csv")
        if len({(r["method"], r["date"], r["scenario"]) for r in weekly}) != len(weekly):
            raise ValueError("duplicate original weekly key")
        if len({(r["method"], r["date"]) for r in daily}) != len(daily):
            raise ValueError("duplicate original daily key")
        return index, books, weekly, daily

    def prepare(self, cohort):
        """Write only new opt-in configs and a selection pin."""
        index, books, _, _ = self.originals(cohort)
        by_key = {job["key"]: row for job, row in zip(index["jobs"], books)}
        selected = []
        for job in select_numeric_jobs(index["jobs"]):
            row = by_key[job["key"]]
            if row["status"] != "refused" or set(row["reason"].split("; ")) != set(job["preflight_refusals"]):
                raise ValueError("refusal row differs from pinned task-index")
            doc, issues = self._derived_document(cohort, job)
            if issues:
                selected.append({"key": job["key"], "status": "opt_in_refused",
                                 "reasons": issues, "original_config_sha256": job["config_sha256"]})
                continue
            dest = self.output_root / cohort / "configs" / (job["key"] + "-stable-tail-v1.json")
            _write_json(dest, doc)
            new_hash = sha256(dest)
            if new_hash == job["config_sha256"]:
                raise ValueError("opt-in config identity did not change")
            selected.append({"key": job["key"], "status": "eligible",
                             "config": str(dest), "config_sha256": new_hash,
                             "original_config_sha256": job["config_sha256"],
                             "date": job["date"], "scenario": job["scenario"]})
        if len(selected) != self.expected_envelope or sum(
                j["status"] == "eligible" for j in selected) != self.expected_eligible:
            raise ValueError("unexpected opt-in numeric preflight count")
        selection = {"schema_version": 1, "cohort": cohort,
                     "original_results_pin_sha256": self.pin_sha256,
                     "input_sha256": self._input_hashes(cohort), "jobs": selected}
        _write_json(self.output_root / cohort / "selection.json", selection)
        return selection

    def execute(self, cohort):
        """Run only selected configs; a started attempt blocks duplicate execution."""
        selection = self._selection(cohort)
        index, _, _, _ = self.originals(cohort)
        self._validate_selection(cohort, index, selection)
        for item in selection["jobs"]:
            if item["status"] != "eligible":
                continue
            config = Path(item["config"])
            if sha256(config) != item["config_sha256"]:
                raise ValueError("opt-in config SHA-256 mismatch")
            attempt = self.output_root / cohort / "attempts" / (item["key"] + ".json")
            started = attempt.with_suffix(".started.json")
            if attempt.exists():
                self._verify_attempt(item, _read_json(attempt))
                continue
            if started.exists():
                raise RuntimeError(f"started attempt requires reconciliation: {started}")
            doc = load_document(str(config))
            _write_json(started, {"status": "started", "config_sha256": item["config_sha256"],
                                  "document_hash": doc.hash})
            ran = run_document(doc, asof=item["date"], journal=False)
            if ran.state != "ran":
                raise RuntimeError(f"stress pipeline did not run: {ran.run_dir}")
            attested = RunAttestation(str(ran.run_dir)).attested_output("stress", "report", doc.hash)
            report = resolve_json_artifact(str(ran.run_dir), attested)
            if report["scenarios"][0]["name"] != item["scenario"] or len(report["scenarios"]) != 1:
                raise ValueError("scenario report mismatch")
            resolved = _read_json(Path(ran.run_dir) / "resolved.json")
            record = {"status": "completed", "config_sha256": item["config_sha256"],
                      "document_hash": doc.hash, "run_dir": str(ran.run_dir),
                      "run_hash": resolved["run_hash"], "report_sha256": attested["sha256"],
                      "report": report}
            _write_json(attempt, record)

    @staticmethod
    def _verify_attempt(item, record):
        if record["config_sha256"] != item["config_sha256"]:
            raise ValueError("attempt config SHA-256 mismatch")
        if sha256(item["config"]) != item["config_sha256"]:
            raise ValueError("opt-in config SHA-256 mismatch")
        if load_document(item["config"]).hash != record["document_hash"]:
            raise ValueError("attempt document hash mismatch")
        attested = RunAttestation(record["run_dir"]).attested_output(
            "stress", "report", record["document_hash"])
        if attested["sha256"] != record["report_sha256"]:
            raise ValueError("attempt report SHA-256 mismatch")
        resolved = _read_json(Path(record["run_dir"]) / "resolved.json")
        if resolved["run_hash"] != record["run_hash"]:
            raise ValueError("attempt run hash mismatch")
        report = resolve_json_artifact(record["run_dir"], attested)
        if report["scenarios"][0]["name"] != item["scenario"] or len(report["scenarios"]) != 1:
            raise ValueError("attempt scenario mismatch")
        if item["scenario"] == "unchanged" and abs(report["scenarios"][0]["pnl"]) > 1e-7:
            raise ValueError("zero-shock book mark changed")
        if report != record["report"]:
            raise ValueError("attempt report content mismatch")
        return report

    def aggregate(self, cohort):
        """Rebuild all common-horizon account rows with exact book lineage."""
        index, books, original_weekly, daily = self.originals(cohort)
        selection = self._selection(cohort)
        self._validate_selection(cohort, index, selection)
        for item in selection["jobs"]:
            if item["status"] == "eligible":
                attempt = self.output_root / cohort / "attempts" / (item["key"] + ".json")
                if not attempt.exists() or _read_json(attempt).get("status") != "completed":
                    raise ValueError("all eligible opt-in attempts must complete before aggregation")
                self._verify_attempt(item, _read_json(attempt))
        _, artifact_index = self._artifact_index()
        chosen = {item["key"]: item for item in selection["jobs"]}
        rows = []
        for job, original in zip(index["jobs"], books):
            opt_in = chosen.get(job["key"], {})
            row = dict(original, key=job["key"], original_evidence=dict(original),
                       opt_in_refusals=opt_in.get("reasons", []))
            config = _read_json(job["config"])
            row["leg_lineage"] = [{
                "leg_id": leg["name"], "source_quote_key": {
                    "symbol": job["ticker"], "quote_date": job["date"],
                    "expiry": job["expiry"], "right": leg["right"],
                    "strike": leg["strike"]}, "config_iv": leg["iv"]}
                for leg in config["pipeline"]["stress"]["params"]["legs"]]
            if original["status"] == "priced":
                wrapper = self.original_root / cohort / "stress/reports" / (job["key"] + ".json")
                evidence = _read_json(wrapper)
                wrapper_key = f"{cohort}/stress/reports/{job['key']}.json"
                wrapper_pin = artifact_index["reproducibility_files"].get(wrapper_key)
                if not wrapper_pin or sha256(wrapper) != wrapper_pin["sha256"]:
                    raise ValueError("original report wrapper SHA-256 mismatch")
                if evidence["config_sha256"] != job["config_sha256"] or evidence["report_sha256"] != original["report_sha256"]:
                    raise ValueError("original report wrapper mismatch")
                if evidence["document_hash"] != original["document_hash"] or evidence["run_hash"] != original["run_hash"]:
                    raise ValueError("original report identity mismatch")
                attested = RunAttestation(evidence["run_dir"]).attested_output(
                    "stress", "report", evidence["document_hash"])
                if attested["sha256"] != original["report_sha256"] or attested["sha256"] != evidence["report_sha256"]:
                    raise ValueError("original report attestation mismatch")
                report = resolve_json_artifact(evidence["run_dir"], attested)
                if report != evidence["report"]:
                    raise ValueError("original report content mismatch")
                if job["scenario"] == "unchanged" and abs(report["scenarios"][0]["pnl"]) > 1e-7:
                    raise ValueError("zero-shock book mark changed")
            elif opt_in.get("status") == "opt_in_refused":
                row["reason"] = "; ".join([original["reason"], *opt_in["reasons"]])
            elif opt_in.get("status") == "eligible":
                attempt = self.output_root / cohort / "attempts" / (job["key"] + ".json")
                if attempt.exists() and _read_json(attempt).get("status") == "completed":
                    record = _read_json(attempt)
                    report = self._verify_attempt(chosen[job["key"]], record)
                    side = report["scenarios"][0]
                    row.update(status="priced", reason="",
                               model_base_value_usd=report["base_value"],
                               scenario_full_value_usd=side["value"],
                               model_mark_change_usd=side["pnl"],
                               config=chosen[job["key"]]["config"],
                               config_sha256=chosen[job["key"]]["config_sha256"],
                               document_hash=record["document_hash"],
                               run_dir=record["run_dir"], run_hash=record["run_hash"],
                               report_sha256=record["report_sha256"])
                else:
                    row["reason"] += "; opt_in_attempt_not_completed"
            if row["status"] == "priced":
                marks = {leg["name"]: leg for leg in report["scenarios"][0]["legs"]}
                if set(marks) != {leg["leg_id"] for leg in row["leg_lineage"]}:
                    raise ValueError("report leg lineage mismatch")
                for leg in row["leg_lineage"]:
                    mark = marks[leg["leg_id"]]
                    leg.update(base_mark=mark["base_mark"],
                               scenario_mark=mark["scenario_mark"], pnl=mark["pnl"])
            rows.append(row)
        groups = defaultdict(list)
        for row in rows:
            if row["kind"] == "weekly_open_book":
                groups[(row["cohort"], row["method"], row["date"], row["scenario"])].append(row)
        daily_by_key = {(r["cohort"], r["method"], r["date"]): r for r in daily}
        results = []
        for old in original_weekly:
            key = (cohort, old["method"], old["date"])
            if key not in daily_by_key:
                raise ValueError("weekly row lacks daily account basis")
            related = groups[(cohort, old["method"], old["date"], old["scenario"])]
            result = aggregate_weekly(daily_by_key[key], old["scenario"], related)
            result["original_evidence"] = dict(old)
            if abs(result["cash_usd"] - float(old["cash_usd"])) > 1e-7:
                raise ValueError("original weekly cash basis mismatch")
            if result["held_books"] != int(old["held_books"]):
                raise ValueError("original weekly count mismatch")
            if (cohort, old["method"], old["date"], old["scenario"]) == (
                    "broad212", "w1_003_shock10_lp", "2019-06-17", "down_12_vol_up_15"):
                if result["status"] != "complete" or abs(
                        result["hypothetical_model_equity_change_usd"] + 116535.08322135845) > 0.005:
                    raise ValueError("June 17 three-book control mismatch")
            results.append(result)
        out = self.output_root / cohort
        _write_json(out / "book-coverage.json", rows)
        self._write_csv(out / "book-coverage.csv", rows)
        _write_json(out / "account-coverage.json", results)
        self._write_csv(out / "account-coverage.csv", results)
        pin = {"schema_version": 1, "cohort": cohort,
               "original_results_pin_sha256": self.pin_sha256,
               "input_sha256": self._input_hashes(cohort),
               "selection_sha256": sha256(out / "selection.json"),
               "book_coverage_sha256": sha256(out / "book-coverage.json"),
               "book_coverage_csv_sha256": sha256(out / "book-coverage.csv"),
               "account_coverage_sha256": sha256(out / "account-coverage.json"),
               "account_coverage_csv_sha256": sha256(out / "account-coverage.csv"),
               "attempts": {item["key"]: sha256(out / "attempts" / (item["key"] + ".json"))
                            for item in selection["jobs"] if (out / "attempts" / (item["key"] + ".json")).exists()},
               "started_attempts": {item["key"]: sha256(out / "attempts" / (item["key"] + ".started.json"))
                                    for item in selection["jobs"] if (out / "attempts" / (item["key"] + ".started.json")).exists()}}
        _write_json(out / "coverage-pins.json", pin)
        return results


def main():
    """Run an explicit driver stage from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "execute", "aggregate"))
    parser.add_argument("cohort", choices=("limited36", "broad212"))
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--original-root", required=True)
    parser.add_argument("--results-pins", required=True)
    parser.add_argument("--results-pins-sha256", required=True)
    parser.add_argument("--expected-envelope", type=int, required=True)
    parser.add_argument("--expected-eligible", type=int, required=True)
    args = parser.parse_args()
    driver = CoverageDriver(args.output_root, args.original_root, args.results_pins,
                            args.results_pins_sha256, args.expected_envelope,
                            args.expected_eligible)
    getattr(driver, args.stage)(args.cohort)


if __name__ == "__main__":
    main()
