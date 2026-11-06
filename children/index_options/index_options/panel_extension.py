"""Extend the full-span panel through 2025-11-28 with the normal builder, 166 locked names (ADR-0298, proposed).

The steps are the ones that built the through-2023 frame (converter, onboarding, raw-chain prepare, panel in
batches, stitch), run from that build's own documents with two things changed: the bound and the scratch root.
Every row dated or settling at or past 2025-11-29 stays out, so no 2026 price is read. ``verify`` then proves the
rows of the through-2023 frame are unchanged, column by column, by hash; it shows names, counts and dates only.

Usage: ``python -m index_options.panel_extension plan|tables|panel|verify RUN.json``. Each stage resumes.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError
from dskit.pipeline.node import atomic_write

from .quarterly_forecasts import CFloor, CodeTree, FOREVER_SEAL, WINDOWS_DRIVES, name_exceptions, require_private
from .strike_region_scores import METRIC_SEAL
from .ticker_lock import DEFAULT_LOCK, TickerLock

__all__ = ["PanelExtension", "main", "rebound", "stitch", "verify_overlap"]

KEY_COLUMNS = ["symbol", "quote_date", "expiry"]
DATE_COLUMNS = ["quote_date", "settlement_date", "expiry"]
BOUND_KEYS = {"end_before", "holdout_start"}
#: Agent estimates for the job plan: the through-2023 build logs show 8.6 GB of tables for 7 years, so about 11 GB for 9.
TABLES_GB, PANEL_GB = 11, 4


def rebound(node, old_root, new_root, path=""):
    """Return a copy of a build document with the bound and the scratch root moved.

    Raises
    ------
    ValueError
        The old boundary appears under a key this rule does not own: it must be read, not guessed.
    """
    if isinstance(node, dict):
        return {key: rebound(value, old_root, new_root, f"{path}.{key}") if key not in BOUND_KEYS or value != METRIC_SEAL
                else FOREVER_SEAL for key, value in node.items()}
    if isinstance(node, list):
        return [rebound(value, old_root, new_root, path) for value in node]
    if isinstance(node, str):
        if METRIC_SEAL in node and not path.endswith("notes"):
            raise ValueError(f"the old boundary sits at {path}: not moved by this rule")
        return node.replace(old_root, new_root)
    return node


def stitch(paths, names, dest, like):
    """Join the batches into one frame with the through-2023 frame's columns, order and types.

    Parameters
    ----------
    paths : list of str
        One panel frame per batch, each with only its own names' one-hot columns.
    names : list of str
        Every locked name.
    dest : str
        The file to write.
    like : pyarrow.Schema
        The through-2023 frame's schema: the one-hot columns are filled with zero, and a column a batch stored
        as an integer because it had no gap is widened as that frame did.

    Returns
    -------
    int
        Rows written.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    onehot = {"is_"+name for name in names}
    if set(like.names) & onehot != onehot:
        raise ValueError("the through-2023 frame lacks a locked one-hot column")
    writer, rows = None, 0
    try:
        for path in paths:
            source = pq.ParquetFile(path)
            if set(source.schema_arrow.names)-onehot != set(like.names)-onehot:
                raise IntegrityError(f"batch columns differ from the through-2023 frame: {path}")
            for group in range(source.num_row_groups):
                table = source.read_row_group(group)
                columns = [table[name].cast(like.field(name).type) if name in table.column_names
                           else pa.array([0]*table.num_rows, type=like.field(name).type) for name in like.names]
                out = pa.table(columns, schema=like.remove_metadata())
                if writer is None:
                    writer = pq.ParquetWriter(dest, out.schema, compression="zstd")
                writer.write_table(out)
                rows += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    return rows


def _column_hash(values):
    """Hash one aligned column's values: bit-exact, one canonical NaN and an explicit null mask."""
    import numpy as np
    import pandas as pd

    series = values.to_pandas()
    if pd.api.types.is_float_dtype(series.dtype):
        array = series.to_numpy(float).copy()
        array[np.isnan(array)] = np.nan
        return hashlib.sha256(array.tobytes()).hexdigest()
    hashed = pd.util.hash_pandas_object(series.astype(object).where(series.notna(), "\0null"), index=False)
    return hashlib.sha256(hashed.to_numpy().tobytes()).hexdigest()


def _added_quarters_missing(added, symbols, exceptions):
    """Return ``{name: [quarters]}`` for each locked name absent from a quarter that holds added rows."""
    import pandas as pd

    quarter = pd.to_datetime(added["quote_date"]).dt.to_period("Q").astype(str)
    every = set(quarter)
    seen = quarter.groupby(added["symbol"]).agg(set).to_dict()
    return {name: sorted(every-seen[name]) if name in seen else sorted(every) or ["all"]
            for name in symbols if name not in exceptions and (name not in seen or every-seen[name])}


def verify_overlap(old, new, symbols, exceptions=None):
    """Prove the through-2023 frame's rows are unchanged in the extended frame; no value leaves this function.

    Returns
    -------
    dict
        ``passed``, counts, dates, the differing column NAMES and one hash per column, and per name the added
        rows' count and any added quarter it is absent from: no name may vanish (``exceptions``: the owner's).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    report = {"passed": False, "differing_columns": [], "column_sha256": {}}
    schema_old, schema_new = pq.read_schema(old).remove_metadata(), pq.read_schema(new).remove_metadata()
    report["schema_equal"] = schema_old.equals(schema_new)
    if not report["schema_equal"]:
        report["differing_columns"] = sorted(c for c in set(schema_old.names) | set(schema_new.names)
                                             if c not in schema_old.names or c not in schema_new.names
                                             or schema_old.field(c).type != schema_new.field(c).type)
        return report
    fields = list(dict.fromkeys([*KEY_COLUMNS, *DATE_COLUMNS]))
    keys_old = pq.read_table(old, columns=fields).to_pandas()
    keys_new = pq.read_table(new, columns=fields).to_pandas()
    report["rows_old"], report["rows_new"] = len(keys_old), len(keys_new)
    report["last_date"] = {c: str(keys_new[c].max()) for c in DATE_COLUMNS}
    report["inside_forever_seal"] = all(str(keys_new[c].max()) < FOREVER_SEAL for c in DATE_COLUMNS)
    report["symbols_equal_lock"] = set(keys_new["symbol"].unique()) == set(symbols)
    overlap = (keys_new["quote_date"] < METRIC_SEAL) & (keys_new["settlement_date"] < METRIC_SEAL)
    report["rows_overlap"], report["rows_added"] = int(overlap.sum()), int((~overlap).sum())
    added = keys_new[~overlap]
    counts = added["symbol"].value_counts()
    report["added_rows_by_name"] = {name: int(counts.get(name, 0)) for name in symbols}
    report["added_quarters_missing"] = _added_quarters_missing(added, symbols, exceptions or {})
    report["names_cover_added_rows"] = not report["added_quarters_missing"]
    report["keys_unique"] = not keys_old.duplicated(KEY_COLUMNS).any() and not keys_new.duplicated(KEY_COLUMNS).any()
    keys_old = keys_old.assign(_at=range(len(keys_old))).sort_values(KEY_COLUMNS)
    kept = keys_new[overlap].assign(_at=keys_new.index[overlap]).sort_values(KEY_COLUMNS)
    report["keys_equal"] = (len(keys_old) == len(kept)
                            and keys_old[KEY_COLUMNS].reset_index(drop=True).equals(kept[KEY_COLUMNS].reset_index(drop=True)))
    if not report["keys_equal"] or not report["keys_unique"]:
        return report
    order_old, order_new = pa.array(keys_old["_at"].to_numpy()), pa.array(kept["_at"].to_numpy())
    # One column at a time: the frame is 369 columns wide and only one pair is ever in memory.
    for name in schema_old.names:
        a = pq.read_table(old, columns=[name])[name].take(order_old)
        b = pq.read_table(new, columns=[name])[name].take(order_new)
        left, right = _column_hash(a), _column_hash(b)
        report["column_sha256"][name] = left
        if left != right:
            report["differing_columns"].append(name)
    report["passed"] = (not report["differing_columns"] and report["inside_forever_seal"]
                        and report["symbols_equal_lock"] and report["keys_equal"] and report["names_cover_added_rows"])
    return report


class PanelExtension:
    """One extension run: the build's documents, a fresh scratch root and the final panel's place."""

    KEYS = {"docs_from", "old_root", "work_root", "final_wsl", "old_frame", "old_frame_sha256",
            "python", "repo", "batches"}
    OPTIONAL = {"ticker_lock", "synthetic", "notes", "halt_file", "c_floor_gb", "c_vhdx", "owner_name_exceptions"}

    def __init__(self, config):
        if not self.KEYS <= set(config) or set(config)-self.KEYS-self.OPTIONAL:
            raise ValueError(f"run keys differ: missing {sorted(self.KEYS-set(config))}, "
                             f"unknown {sorted(set(config)-self.KEYS-self.OPTIONAL)}")
        if not config.get("synthetic"):
            if Path(config.get("ticker_lock", DEFAULT_LOCK)).resolve() != DEFAULT_LOCK.resolve():
                raise ValueError("a real extension uses the one locked ticker list (A0009)")
            if config.get("c_floor_gb") is None or not config.get("c_vhdx"):
                raise ValueError("a real extension names c_floor_gb and c_vhdx, the WSL disk file while it is on C:")
        if any(str(config[key]).startswith(WINDOWS_DRIVES) for key in ("final_wsl", "work_root")):
            raise ValueError("the extended panel and its batches hold 2024+ outcome columns: "
                             "they stay in WSL unless the owner rules otherwise")
        self.config = config
        self.work = Path(config["work_root"])
        self.docs = self.work/"docs"
        self.lock = TickerLock.load(config.get("ticker_lock", DEFAULT_LOCK))
        self.exceptions = name_exceptions(config, self.lock.symbols)
        self.c_floor = CFloor(config)
        self.code = CodeTree(config["repo"])
        self.child = Path(config["repo"])/"children"/"index_options"
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                        PYTHONPATH=f"{config['repo']}:{self.child}", OMP_NUM_THREADS="4")

    def plan(self):
        """Write the moved documents; nothing is read or built, and a refusal leaves nothing behind."""
        c = self.config
        self.c_floor.refuse([(self.work, TABLES_GB), (self.work, PANEL_GB)])
        for private in (self.work, c["final_wsl"]):
            require_private(private)
        atomic_write(str(self.work/"run.json"), json.dumps(c, indent=1).encode())
        self.docs.mkdir(parents=True, exist_ok=True)
        out = {}
        for name in ("conv", "prepare", "panel0"):
            source = json.loads((Path(c["docs_from"])/f"{name}.json").read_text())
            moved = rebound(source, c["old_root"], str(self.work))
            self.lock.check(moved)
            target = self.docs/f"{name}.json"
            atomic_write(str(target), json.dumps(moved, indent=1).encode())
            out[name] = str(target)
        study = json.loads((self.docs/"panel0.json").read_text())["study"]["fold_table"]
        if study["holdout_start"] != FOREVER_SEAL:
            raise IntegrityError("the panel's boundary did not move")
        symbols = sorted(self.lock.symbols)
        atomic_write(str(self.work/"symbols.json"), json.dumps(symbols).encode())
        atomic_write(str(self.work/"jobs-panel.json"), json.dumps(self._jobs(), indent=1).encode())
        return out

    def _jobs(self):
        run = str(self.work/"run.json")
        command = f"{self.config['python']} -B -m index_options.panel_extension "
        env = {"PYTHONPATH": f"{self.config['repo']}:{self.child}", "PYTHONDONTWRITEBYTECODE": "1"}
        job = {"cwd": str(self.child), "env": env, "allowed_hosts": ["wsl"], "retries": 0}
        return {"run_id": "panel-extension-20251128", "jobs": [
            {**job, "id": "tables", "command": command+"tables "+run, "cores": 6, "ram_gb": 6, "disk_gb": TABLES_GB,
             "ram_basis": "audit 10_dryrun: converter 2.2-4 GB for all names, prepare 3 GB, one at a time",
             "depends_on": []},
            {**job, "id": "panel", "command": command+"panel "+run, "cores": 2, "ram_gb": 11, "disk_gb": PANEL_GB,
             "ram_basis": "20_panel.log receipts: 10.0 GB per 55-name batch at 636k rows; 5 batches through 2025 hold fewer rows each",
             "depends_on": ["tables"], "serial_reason": "the tables feed every batch; batches feed one stitch"},
            {**job, "id": "verify", "command": command+"verify "+run, "cores": 1, "ram_gb": 2, "disk_gb": 0,
             "ram_basis": "key columns and one column pair at a time at 2.8M rows; measured on synthetic only",
             "depends_on": ["panel"], "serial_reason": "reads the finished panel"}]}

    def _run(self, *args):
        self.code.require_clean(self.config)        # before the build's journal rows, and before a long stage
        halt = self.config.get("halt_file")
        if halt and Path(halt).exists():
            raise RuntimeError(f"HALT file present: {halt}")
        subprocess.run([self.config["python"], "-B", *args], cwd=self.child, env=self.env, check=True,
                       stdout=subprocess.DEVNULL)

    def _step(self, name, action):
        done = self.work/".done"/name
        if done.exists():
            return
        action()
        done.parent.mkdir(exist_ok=True)
        done.write_text("done")

    def _snapshot(self, root, source):
        from dskit.onboarding.snapshot import read_manifest, snapshot_hash

        latest = sorted(glob.glob(str(Path(root)/"raw"/source/"*/")))[-1].rstrip("/")
        return snapshot_hash(read_manifest(latest))

    def _register(self, root, source, connector, config, stream):
        if glob.glob(str(Path(root)/"raw"/source/"*/")):
            return                  # killed after its acquire, before the step's mark: the snapshot stands
        self._run("-m", "dskit.onboarding", "register-source", source, "--catalog-source", source,
                  "--connector", connector, "--config", json.dumps(config), "--activate", "--root", root)
        self._run("-m", "dskit.onboarding", "acquire", "--source", source, "--stream", stream,
                  "--mode", "backfill", "--root", root)

    def _convert(self):
        """Run the converter unless it published; a kill leaves its partial folder, in our own scratch."""
        output = self.work/"exact-expiry-tables"
        partial = self.work/f".{output.name}.partial"
        if output.exists():
            return
        if partial.exists():
            shutil.rmtree(partial)
        self._run("-m", "dskit.pipeline", "run", str(self.docs/"conv.json"), "--asof", "2025-11-28")

    def _init_onboarding(self, root):
        if not (Path(root)/"store"/"store.json").exists():
            self._run("-m", "dskit.onboarding", "init", "--root", root)

    def tables(self):
        """Run the converter, onboarding and raw-chain prepare: the through-2023 build's steps, resumed by step."""
        self.code.require_clean(self.config)
        root, tables = str(self.work/"ob"), str(self.work/"exact-expiry-tables")
        as_of = "2026-11-05T00:00:00+00:00"
        (self.work/"raw_chain").mkdir(parents=True, exist_ok=True)
        self._step("convert", self._convert)
        self._step("init", lambda: self._init_onboarding(root))
        self._step("tables", lambda: self._register(root, "theta-exact-expiry-tables", "localblobs", {
            "path": tables, "as_of": as_of, "include": ["surface.parquet", "lifecycle.parquet", "manifest.json"]}, "files"))
        self._step("archive", lambda: self._register(root, "theta-exact-expiry-archive", "localblobs", {
            "path": tables+"/archive", "as_of": as_of, "include": ["*.parquet"]}, "files"))
        for source, leaf in (("theta-prices-index-daily", "prices"), ("theta-own-iv-index-daily", "own_iv")):
            self._step(source, lambda source=source, leaf=leaf: self._register(root, source, "localtables", {
                "path": f"{tables}/{leaf}", "layout": "directory", "effective_field": "date", "stamp_stem_as": "symbol"},
                "index_daily"))
        self._pin_prepare(root)
        self._step("prepare", lambda: self._run("-m", "index_options.cdf_study", str(self.docs/"prepare.json"), "--stage", "prepare", "--workers", "6"))
        self._step("raw-chain", lambda: self._register(root, "theta-raw-chain-features", "localblobs", {
            "path": str(self.work/"raw_chain"), "as_of": as_of,
            "include": ["raw_chain_features.parquet", "raw_chain_features.parquet.sources.json"]}, "files"))
        panel = json.loads((self.docs/"panel0.json").read_text())
        panel["data"]["chain_features"]["manifest_sha256"] = self._snapshot(root, "theta-raw-chain-features")
        self._pin(panel, root)
        atomic_write(str(self.docs/"panel.json"), json.dumps(panel, indent=1).encode())

    def _pin(self, document, root):
        data = document["data"]
        data["root"] = root
        data["surface"]["manifest_sha256"] = data["lifecycle"]["manifest_sha256"] = self._snapshot(root, "theta-exact-expiry-tables")
        data["archive_root"]["manifest_sha256"] = self._snapshot(root, "theta-exact-expiry-archive")

    def _pin_prepare(self, root):
        prepare = json.loads((self.docs/"prepare.json").read_text())
        self._pin(prepare, root)
        prepare["data"]["chain_features"] = str(self.work/"raw_chain"/"raw_chain_features.parquet")
        atomic_write(str(self.docs/"prepare.json"), json.dumps(prepare, indent=1).encode())

    def _batch(self, names, tag):
        base = json.loads((self.docs/"panel.json").read_text())
        everyone = set(self.lock.symbols)
        out = self.work/tag
        if (out/"panel-cache"/"frame.parquet").exists():
            return str(out/"panel-cache"/"frame.parquet")
        base["data"]["symbols"] = {s: s for s in names}
        base["study"]["features"] = [f for f in base["study"]["features"] if not (f.startswith("is_") and f[3:] in everyone)]+["is_"+s for s in names]
        base["experiment"]["task_features"] = {s: "is_"+s for s in names}
        base["experiment"]["output"], base["study"]["output"] = str(out), str(out)+"-study"
        self.lock.check(base, subset=True)
        document = self.docs/f"{tag}.json"
        atomic_write(str(document), json.dumps(base).encode())
        self._run("-m", "index_options.cdf_study", str(document), "--stage", "panel")
        return str(out/"panel-cache"/"frame.parquet")

    def final_directory(self, size_bytes):
        """Return the panel's place in WSL; refuse if it would take C: past the owner's floor."""
        self.c_floor.refuse([(self.config["final_wsl"], size_bytes/1024**3)])
        return Path(self.config["final_wsl"])

    def _names_cuts(self):
        names, k = list(self.lock.symbols), self.config["batches"]
        return [names[i*len(names)//k:(i+1)*len(names)//k] for i in range(k)]

    def panel(self):
        """Build the batches and stitch them into the final place; a killed stage resumes, a rival refuses."""
        import pyarrow.parquet as pq

        self.code.require_clean(self.config)
        record = self.work/"final.json"
        if record.exists():
            saved = json.loads(record.read_text())
            if AtomicFitStore.file_hash(saved["panel"]) != saved["sha256"]:
                raise IntegrityError("the extended panel differs from its build hash")
            return saved
        paths = [self._batch(part, f"pb{i}") for i, part in enumerate(self._names_cuts())]
        final = self.final_directory(sum(Path(path).stat().st_size for path in paths))
        require_private(final)
        like = pq.read_schema(self.config["old_frame"]).remove_metadata()
        target = final/"frame.parquet"
        partial = target.with_suffix(".partial")
        rows = stitch(paths, list(self.lock.symbols), str(partial), like)
        digest = AtomicFitStore.file_hash(partial)
        if not target.exists():
            os.replace(partial, target)
        elif AtomicFitStore.file_hash(target) != digest:
            raise IntegrityError(f"refusing to overwrite {target}: it is another build")
        else:
            partial.unlink()
        out = {"panel": str(target), "sha256": digest, "rows": rows}
        atomic_write(str(record), json.dumps(out).encode())
        return out

    def verify(self):
        """Run the overlap proof against the pinned through-2023 frame."""
        c = self.config
        code_commit = self.code.require_clean(c)
        if AtomicFitStore.file_hash(c["old_frame"]) != c["old_frame_sha256"]:
            raise IntegrityError("the through-2023 frame differs from its pin")
        final = json.loads((self.work/"final.json").read_text())
        if AtomicFitStore.file_hash(final["panel"]) != final["sha256"]:
            raise IntegrityError("the extended panel differs from its build hash")
        report = verify_overlap(c["old_frame"], final["panel"], self.lock.symbols, self.exceptions)
        report.update(panel_sha256=final["sha256"], old_frame_sha256=c["old_frame_sha256"],
                      synthetic=bool(c.get("synthetic")), owner_name_exceptions=self.exceptions,
                      code_commit=code_commit,
                      ticker_lock_sha256=AtomicFitStore.file_hash(c.get("ticker_lock", DEFAULT_LOCK)))
        atomic_write(str(self.work/"verify.json"), json.dumps(report, indent=1, sort_keys=True).encode())
        if not report["passed"]:
            raise IntegrityError("overlap check failed; see verify.json (names and counts only)")
        return {k: v for k, v in report.items() if k != "column_sha256"}


def main(argv=None):
    """Run one stage; prints names, counts, dates and hashes only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=["plan", "tables", "panel", "verify"])
    parser.add_argument("run")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.run).read_text())
    extension = PanelExtension(config)
    result = getattr(extension, args.verb)()
    print(json.dumps(result if result is not None else {"stage": args.verb, "done": True}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
