"""ADR-0093: bounded parallel fold execution, graduated into the toolkit.

What is pinned here, and why each pin matters:

- the WIDTH comes from the environment, never a document — unset is the
  serial path, empty is refused, an explicit ``workers`` wins;
- the CAP is per process and validated in the PARENT: a cap above a
  finite hard ``RLIMIT_AS`` is refused before anything spawns, and the
  child really does run under it (read back from inside the child);
- there is NO shell and NO ``preexec_fn`` between fork and exec — the
  shim is this interpreter setting its own limit and exec-ing the fold;
- ``run`` returns results in INPUT order whatever the completion order,
  width 1 builds no pool, and a failing fold drops the ones that have
  not started;
- ``measure_one`` is the only memory reading, reports it for exactly one
  child, and refuses a process whose child counter is already used.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

import dskit.pipeline.folds as folds
from dskit.pipeline.folds import BoundedFoldRunner

PY = sys.executable
MIB = 1024**2
GIB = 1024**3
REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
RLIMIT_PROBE = [PY, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS))"]


def _print_after(seconds, text):
    return [PY, "-c", f"import time; time.sleep({seconds}); print({text!r})"]


class TestTheWidth:
    def test_unset_is_the_serial_path(self, monkeypatch):
        monkeypatch.delenv("DSKIT_FOLD_WORKERS", raising=False)
        assert BoundedFoldRunner(None).workers == 1

    def test_the_environment_sets_it(self, monkeypatch):
        monkeypatch.setenv("DSKIT_FOLD_WORKERS", "3")
        assert BoundedFoldRunner(None).workers == 3

    def test_an_explicit_width_overrides_the_environment(self, monkeypatch):
        monkeypatch.setenv("DSKIT_FOLD_WORKERS", "3")
        assert BoundedFoldRunner(None, workers=2).workers == 2

    def test_a_caller_names_its_own_variable(self, monkeypatch):
        monkeypatch.setenv("DSKIT_FOLD_WORKERS", "9")
        monkeypatch.setenv("MY_FOLD_WORKERS", "4")
        assert BoundedFoldRunner(None, env_var="MY_FOLD_WORKERS").workers == 4

    @pytest.mark.parametrize("raw", ["", "0", "-2", "four", "2.5"])
    def test_a_value_that_is_not_a_positive_int_is_refused(self, monkeypatch, raw):
        # An empty value is an accident (``export VAR=``), and silently
        # running serially would hide it.
        monkeypatch.setenv("DSKIT_FOLD_WORKERS", raw)
        with pytest.raises(ValueError, match="DSKIT_FOLD_WORKERS"):
            BoundedFoldRunner(None)

    @pytest.mark.parametrize("workers", [0, -1, True, 2.0, "2"])
    def test_an_explicit_width_must_be_a_positive_int(self, workers):
        with pytest.raises(ValueError, match="workers"):
            BoundedFoldRunner(None, workers=workers)

    @pytest.mark.parametrize("env_var", ["", None, 3])
    def test_the_variable_name_must_be_a_non_empty_string(self, env_var):
        with pytest.raises(ValueError, match="env_var"):
            BoundedFoldRunner(None, workers=1, env_var=env_var)


class TestTheCap:
    @pytest.mark.parametrize("cap", [0, -1, True, 1.5, "1"])
    def test_a_cap_must_be_a_positive_int_or_none(self, cap):
        with pytest.raises(ValueError, match="memory_limit_bytes"):
            BoundedFoldRunner(cap, workers=1)

    def test_none_is_uncapped_and_argv_runs_as_given(self):
        import resource

        out = BoundedFoldRunner(None, workers=1).run([RLIMIT_PROBE])
        assert out[0].stdout.strip() == str(resource.getrlimit(resource.RLIMIT_AS))

    def test_the_child_really_runs_under_the_cap(self):
        # A substring in argv proves nothing; read the limit back from
        # inside the process the shim exec-ed into.
        cap = 512 * MIB
        out = BoundedFoldRunner(cap, workers=1).run([RLIMIT_PROBE])
        assert out[0].stdout.strip() == str((cap, cap))

    def test_the_cap_binds_the_fold_that_runs_under_it(self):
        cap = GIB
        with pytest.raises(RuntimeError, match="MemoryError"):
            BoundedFoldRunner(cap, workers=1).run(
                [[PY, "-c", "b = bytearray(2 * 1024**3); print(len(b))"]]
            )

    def test_a_cap_above_a_finite_hard_limit_is_refused_before_anything_spawns(
        self, monkeypatch
    ):
        import resource

        monkeypatch.setattr(resource, "getrlimit", lambda _which: (MIB, MIB))
        spawned = []

        class Spy(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                spawned.append(index)

        with pytest.raises(ValueError, match="hard"):
            Spy(2 * MIB, workers=1).run([[PY, "-c", "pass"]])
        assert spawned == []

    def test_an_infinite_hard_limit_never_refuses(self, monkeypatch):
        # RLIM_INFINITY is -1 and every default machine reports it: an
        # unguarded comparison would refuse everywhere.
        import resource

        monkeypatch.setattr(
            resource,
            "getrlimit",
            lambda _which: (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        )
        spawned = []

        class Spy(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                spawned.append(index)
                return subprocess.CompletedProcess(argv, 0, "", "")

        Spy(10**15, workers=1).run([[PY, "-c", "pass"]])
        assert spawned == [0]

    def test_a_cap_at_or_below_a_finite_hard_limit_is_allowed(self, monkeypatch):
        import resource

        monkeypatch.setattr(resource, "getrlimit", lambda _which: (MIB, 2 * MIB))
        spawned = []

        class Spy(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                spawned.append(index)
                return subprocess.CompletedProcess(argv, 0, "", "")

        Spy(2 * MIB, workers=1).run([[PY, "-c", "pass"]])
        assert spawned == [0]

    def test_the_cap_is_the_same_for_every_fold_never_divided(self):
        cap = 512 * MIB
        out = BoundedFoldRunner(cap, workers=3).run([RLIMIT_PROBE] * 3)
        assert [c.stdout.strip() for c in out] == [str((cap, cap))] * 3


class TestTheShim:
    def test_no_shell_and_no_preexec_fn(self, monkeypatch):
        calls = []
        real = subprocess.run

        def spy(command, **kwargs):
            calls.append((list(command), dict(kwargs)))
            return real(command, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        BoundedFoldRunner(GIB, workers=1).run([[PY, "-c", "pass"]])
        ((command, kwargs),) = calls
        assert "preexec_fn" not in kwargs
        assert not kwargs.get("shell")
        assert command[0] == PY, "the shim is this interpreter, not a shell"
        assert command[-3:] == [PY, "-c", "pass"], "the fold's own argv rides last"
        assert not any(part.endswith("/sh") or part == "sh" for part in command)

    def test_resource_is_imported_inside_the_methods_that_touch_it(self):
        tree = ast.parse(pathlib.Path(folds.__file__).read_text(encoding="utf-8"))
        top = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        top |= {node.module for node in tree.body if isinstance(node, ast.ImportFrom)}
        assert "resource" not in top

    def test_the_seam_is_reached_by_path_not_the_package_surface(self):
        import dskit.pipeline

        assert not hasattr(dskit.pipeline, "BoundedFoldRunner")
        assert folds.__all__ == ["BoundedFoldRunner"]


class TestTheRun:
    def test_results_come_back_in_input_order_whatever_finishes_first(self):
        commands = [_print_after(0.3 - 0.1 * i, f"fold {i}") for i in range(3)]
        out = BoundedFoldRunner(None, workers=3).run(commands)
        assert all(isinstance(c, subprocess.CompletedProcess) for c in out)
        assert [c.stdout.strip() for c in out] == ["fold 0", "fold 1", "fold 2"]

    def test_folds_really_run_at_the_same_time(self, tmp_path):
        script = textwrap.dedent(
            """
            import sys, time
            path = sys.argv[1]
            open(path, "w").write(f"{time.time()} ")
            time.sleep(0.4)
            open(path, "a").write(f"{time.time()}")
            """
        )
        commands = [[PY, "-c", script, str(tmp_path / f"{i}.txt")] for i in range(3)]
        BoundedFoldRunner(None, workers=3).run(commands)
        windows = [
            tuple(float(v) for v in (tmp_path / f"{i}.txt").read_text().split())
            for i in range(3)
        ]
        assert max(start for start, _end in windows) < min(
            end for _start, end in windows
        ), "the folds ran one after another"

    def test_width_one_is_the_serial_path_with_no_pool(self, monkeypatch):
        import concurrent.futures

        def boom(*_args, **_kwargs):
            raise AssertionError("a pool was built at width 1")

        monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", boom)
        out = BoundedFoldRunner(None, workers=1).run(
            [[PY, "-c", "print(1)"], [PY, "-c", "print(2)"]]
        )
        assert [c.stdout.strip() for c in out] == ["1", "2"]

    def test_a_failing_fold_raises_with_the_output_tail(self):
        with pytest.raises(RuntimeError) as err:
            BoundedFoldRunner(None, workers=1).run(
                [[PY, "-c", "import sys; print('the tail line'); sys.exit(7)"]]
            )
        assert "exited 7" in str(err.value)
        assert "the tail line" in str(err.value)

    def test_stderr_is_part_of_the_tail(self):
        with pytest.raises(RuntimeError, match="boom on stderr"):
            BoundedFoldRunner(None, workers=1).run(
                [[PY, "-c", "import sys; sys.stderr.write('boom on stderr'); sys.exit(1)"]]
            )

    def test_a_failing_fold_drops_the_folds_that_have_not_started(self, tmp_path):
        script = "import sys, time; open(sys.argv[1], 'w').write('x'); time.sleep(0.3)"
        commands = [[PY, "-c", script, str(tmp_path / f"{i}.txt")] for i in range(8)]
        commands[0] = [PY, "-c", "import sys; sys.exit(3)"]
        with pytest.raises(RuntimeError, match="exited 3"):
            BoundedFoldRunner(None, workers=2).run(commands)
        assert len(list(tmp_path.glob("*.txt"))) < 7, "every fold ran after one failed"

    def test_cwd_and_env_are_batch_wide(self, tmp_path):
        probe = [PY, "-c", "import os; print(os.getcwd()); print(os.environ['FOLD_PROBE'])"]
        env = {**os.environ, "FOLD_PROBE": "yes"}
        out = BoundedFoldRunner(None, workers=2).run(
            [probe, probe], cwd=str(tmp_path), env=env
        )
        expected = [os.path.realpath(str(tmp_path)), "yes"]
        assert [c.stdout.split() for c in out] == [expected, expected]

    @pytest.mark.parametrize(
        "commands",
        [None, "ls", [], [[]], [["x", 1]], [[PY, "-c", "pass"], "ls"], [("x",)]],
    )
    def test_commands_must_be_a_non_empty_list_of_argv_lists(self, commands):
        with pytest.raises(ValueError, match="commands"):
            BoundedFoldRunner(None, workers=1).run(commands)

    def test_a_slow_first_fold_does_not_hold_the_pool_hostage(self):
        # Input ORDER is the return order; it is not a serialisation.
        commands = [_print_after(0.4, "slow"), _print_after(0.0, "quick")]
        t0 = time.monotonic()
        out = BoundedFoldRunner(None, workers=2).run(commands)
        assert time.monotonic() - t0 < 0.9
        assert [c.stdout.strip() for c in out] == ["slow", "quick"]


class TestTheSpawnHook:
    def test_spawn_is_the_seam_a_subclass_supplies(self):
        seen = []

        class Recording(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                seen.append((index, list(argv), cwd, env))
                return subprocess.CompletedProcess(argv, 0, f"spawned {index}", "")

        out = Recording(None, workers=2).run(
            [["a"], ["b"], ["c"]], cwd="/x", env={"K": "v"}
        )
        assert [c.stdout for c in out] == ["spawned 0", "spawned 1", "spawned 2"]
        assert sorted(seen) == [
            (0, ["a"], "/x", {"K": "v"}),
            (1, ["b"], "/x", {"K": "v"}),
            (2, ["c"], "/x", {"K": "v"}),
        ]

    def test_the_default_spawn_returns_the_completed_process(self):
        done = BoundedFoldRunner(None, workers=1).spawn(
            0, [PY, "-c", "print('hi')"], None, None
        )
        assert isinstance(done, subprocess.CompletedProcess)
        assert done.returncode == 0
        assert done.stdout.strip() == "hi"


class TestMeasureOne:
    def test_it_reports_the_peak_of_the_one_child_in_a_fresh_process(self):
        # RUSAGE_CHILDREN is process-global and this pytest process has
        # reaped children already, so the positive case needs a fresh
        # interpreter — the precondition the seam enforces.
        script = textwrap.dedent(
            """
            import json, sys
            from dskit.pipeline.folds import BoundedFoldRunner

            runner = BoundedFoldRunner(4 * 1024**3, workers=1)
            child = (
                "b = bytearray(200 * 1024**2)\\n"
                "for i in range(0, len(b), 4096): b[i] = 1\\n"
                "print('ok')"
            )
            done, peak = runner.measure_one([sys.executable, "-c", child])
            print(json.dumps({"stdout": done.stdout.strip(), "rc": done.returncode,
                              "peak": peak}))
            """
        )
        out = subprocess.run(
            [PY, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert out.returncode == 0, out.stderr
        report = json.loads(out.stdout.strip().splitlines()[-1])
        assert report["stdout"] == "ok"
        assert report["rc"] == 0
        assert isinstance(report["peak"], int)
        assert report["peak"] >= 200 * MIB

    def test_a_contaminated_counter_is_refused_before_spawning(self, monkeypatch):
        import resource

        monkeypatch.setattr(
            resource, "getrusage", lambda _who: SimpleNamespace(ru_maxrss=123)
        )
        spawned = []

        class Spy(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                spawned.append(index)

        with pytest.raises(ValueError, match="ru_maxrss"):
            Spy(None, workers=1).measure_one([PY, "-c", "pass"])
        assert spawned == []

    def test_this_process_is_already_contaminated_and_the_seam_says_so(self):
        # The test above proves the refusal against a fake counter; this
        # one proves it against the REAL one. TestTheRun has reaped
        # children in this process, so the counter is nonzero here.
        with pytest.raises(ValueError, match="ru_maxrss"):
            BoundedFoldRunner(None, workers=1).measure_one([PY, "-c", "pass"])

    def test_it_delegates_to_run_and_the_spawn_hook(self, monkeypatch):
        import resource

        monkeypatch.setattr(
            resource, "getrusage", lambda _who: SimpleNamespace(ru_maxrss=0)
        )
        seen = []

        class Spy(BoundedFoldRunner):
            def spawn(self, index, argv, cwd, env):
                seen.append((index, list(argv), cwd, env))
                return subprocess.CompletedProcess(argv, 0, "done", "")

        done, peak = Spy(GIB, workers=3).measure_one(["x"], cwd="/c", env={"E": "1"})
        assert seen == [(0, ["x"], "/c", {"E": "1"})]
        assert done.stdout == "done"
        assert peak == 0

    def test_it_validates_the_cap_in_the_parent_like_run(self, monkeypatch):
        import resource

        monkeypatch.setattr(
            resource, "getrusage", lambda _who: SimpleNamespace(ru_maxrss=0)
        )
        monkeypatch.setattr(resource, "getrlimit", lambda _which: (MIB, MIB))
        with pytest.raises(ValueError, match="hard"):
            BoundedFoldRunner(2 * MIB, workers=1).measure_one([PY, "-c", "pass"])
