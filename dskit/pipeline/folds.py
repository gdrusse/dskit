"""Bounded parallel fold execution — the seam a walk's folds run through.

A walk-forward's folds are independent by construction: each carries its
own cutoff and its own single-fold document, so running them one after
another is pure wall-clock waste. :class:`BoundedFoldRunner` runs a batch
of argv commands at a width the ENVIRONMENT chooses, each under one
address-space cap, and hands the results back in input order (ADR-0093).

Three rulings shape it. The cap is a caller-supplied parameter with no
default here — a tier-1 number would be exactly the hardcoded threshold
this toolkit forbids — and it is never divided between folds:
``RLIMIT_AS`` bounds address space, not resident memory, and a divided
cap refuses mappings a measured peak never sees. The cap is applied by a
``setrlimit`` + ``exec`` shim rather than by ``preexec_fn`` (which bars
``posix_spawn`` and runs Python between fork and exec, the pattern
CPython documents as unsafe with a pool's threads) or by a shell (an
interpreter with its own quoting surface): the shim is this interpreter
setting its OWN limit after its own exec and then exec-ing the fold, so
nothing Python happens in the parent between fork and exec. And the
width is read from an environment variable, never from a document: fold
count is a property of the machine, and a graded knob would move a
document's identity each time it was tuned.

The seam measures no memory in ``run``: ``RUSAGE_CHILDREN.ru_maxrss`` is
process-global and monotone, so under a pool a fold would report
whichever sibling peaked highest. :meth:`BoundedFoldRunner.measure_one`
is the one reading, for exactly one child. It reads that counter in a
fresh measuring interpreter whose only child is the fold, never in the
caller, so a process may measure any number of times, in any order,
whatever it has reaped before (ADR-0093 amendment, 2026-09-26).

``resource`` is imported inside the methods that touch it, so this
module imports on any platform.
"""

from __future__ import annotations

import concurrent.futures
import copy
import os
import re
import secrets
import subprocess
import sys

__all__ = ["BoundedFoldRunner"]

#: How many trailing characters of a failed fold's output ride in its error.
_OUTPUT_TAIL = 4000

#: The shim, run as ``python -I -c _SHIM <cap> <argv...>``: set this
#: process's own address-space limit, then become the fold. ``-I`` keeps
#: the shim's own start-up off ``PYTHON*`` variables and the cwd; the
#: fold it execs into inherits the environment untouched.
_SHIM = (
    "import os, resource, sys; cap = int(sys.argv[1]); "
    "resource.setrlimit(resource.RLIMIT_AS, (cap, cap)); "
    "os.execvp(sys.argv[2], sys.argv[2:])"
)

#: The measuring wrapper, run as ``python -I -c _MEASURE <nonce> <command...>``
#: where ``<command...>`` is exactly what ``run`` would spawn (the capped
#: shim, or the bare argv when uncapped). It is a FRESH interpreter, so its
#: own ``RUSAGE_CHILDREN`` starts at zero whatever the caller has reaped;
#: it still refuses a nonzero counter, then runs the command as its one
#: child (cwd, environment and streams inherited) and, on a clean exit,
#: prints that child's ``ru_maxrss`` behind the nonce. It exits as the
#: child did, re-raising a fatal signal so the caller sees the same code
#: (SIGKILL, the OOM killer's, takes no handler, hence the guard).
_MEASURE = (
    "import os, resource, signal, subprocess, sys\n"
    "before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
    "if before:\n"
    "    sys.exit(f'RUSAGE_CHILDREN.ru_maxrss is already {before}: this process "
    "has reaped a child, so a one-child memory reading is impossible here')\n"
    "code = subprocess.call(sys.argv[2:])\n"
    "if code < 0:\n"
    "    try:\n"
    "        signal.signal(-code, signal.SIG_DFL)\n"
    "    except (OSError, ValueError):\n"
    "        pass\n"
    "    os.kill(os.getpid(), -code)\n"
    "    sys.exit(128 - code)\n"
    "if code == 0:\n"
    "    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
    "    sys.stdout.write(f'\\n{sys.argv[1]} {peak}\\n')\n"
    "sys.exit(code)\n"
)

#: ``ru_maxrss`` is reported in kilobytes on Linux and in bytes on macOS.
_RU_MAXRSS_UNIT = 1 if sys.platform == "darwin" else 1024


def _declared_width(workers, env_var):
    """Resolve the fold width: an explicit int, else the environment, else 1."""
    if workers is not None:
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError(f"workers must be a positive int, got {workers!r}")
        return workers
    raw = os.environ.get(env_var)
    if raw is None:
        return 1
    try:
        width = int(raw)
    except ValueError:
        width = 0
    if width < 1:
        raise ValueError(f"{env_var} must be a positive integer, got {raw!r}")
    return width


def _check_commands(commands):
    """Refuse anything but a non-empty list of argv lists of strings."""
    ok = (
        isinstance(commands, list)
        and bool(commands)
        and all(
            isinstance(argv, list)
            and bool(argv)
            and all(isinstance(part, str) for part in argv)
            for argv in commands
        )
    )
    if not ok:
        raise ValueError("commands must be a non-empty list of argv lists of strings")


def _split_reading(stdout, nonce):
    """Split the measuring wrapper's line off ``stdout``: (fold output, ru_maxrss)."""
    head, marker, tail = (
        stdout.rpartition(f"\n{nonce} ") if isinstance(stdout, str) else ("", "", "")
    )
    reading = re.match(r"(\d+)\n", tail) if marker else None
    if reading is None:
        raise ValueError(
            "the measured spawn returned no peak reading: a spawn override must "
            "run its fold through the default spawn, which captures stdout as text"
        )
    return head + tail[reading.end():], int(reading.group(1))


class BoundedFoldRunner:
    """Run a batch of fold commands at a bounded width, each under one cap.

    Parameters
    ----------
    memory_limit_bytes : int or None
        Address-space cap (``RLIMIT_AS``) applied to EVERY spawned fold,
        never divided between them; ``None`` runs the folds uncapped.
        Required, with no default here: a cap is a study's number. At
        most ``sys.maxsize``, the platform's C ``long`` bound.
    workers : int or None
        How many folds may run at once. ``None`` (the default) reads
        ``env_var`` from the process environment: unset means 1, the
        serial path; an empty value is refused rather than defaulted,
        because ``export VAR=`` is an accident. An explicit positive int
        wins over the environment. Never source it from a graded
        document.
    env_var : str
        The environment variable naming the width, ``DSKIT_FOLD_WORKERS``
        by default; a caller keeps its own documented knob by naming it.

    Raises
    ------
    ValueError
        When the cap, the width or the variable name is malformed.

    Examples
    --------
    Run two derived walks under a 17 GiB cap at the machine's declared
    width, reading results back in input order::

        runner = BoundedFoldRunner(17 * 1024**3, env_var="MY_FOLD_WORKERS")
        done = runner.run(
            [
                [sys.executable, "-m", "dskit.pipeline", "walkforward", "a.json"],
                [sys.executable, "-m", "dskit.pipeline", "walkforward", "b.json"],
            ],
            cwd="/my/child",
        )
        [d.returncode for d in done]  # [0, 0]
    """

    #: Set only on :meth:`measure_one`'s per-call copy: wrap the command in
    #: the measuring interpreter and mark its reading with this nonce.
    _measure_nonce = None

    def __init__(self, memory_limit_bytes, workers=None, env_var="DSKIT_FOLD_WORKERS"):
        if memory_limit_bytes is not None and (
            isinstance(memory_limit_bytes, bool)
            or not isinstance(memory_limit_bytes, int)
            or memory_limit_bytes < 1
        ):
            raise ValueError(
                "memory_limit_bytes must be a positive int or None, "
                f"got {memory_limit_bytes!r}"
            )
        # Above the C long range the cap passes _check_cap — the hard
        # RLIMIT_AS is RLIM_INFINITY — and then raises OverflowError
        # inside the shim, surfacing as a fold failure. A cap failure is
        # configuration, never wrapped (ADR-0093).
        if memory_limit_bytes is not None and memory_limit_bytes > sys.maxsize:
            raise ValueError(
                "memory_limit_bytes must be at most the platform C long "
                f"{sys.maxsize}, got {memory_limit_bytes!r}"
            )
        if not isinstance(env_var, str) or not env_var:
            raise ValueError(f"env_var must be a non-empty string, got {env_var!r}")
        self.memory_limit_bytes = memory_limit_bytes
        self.env_var = env_var
        self.workers = _declared_width(workers, env_var)

    def run(self, commands, cwd=None, env=None):
        """Run every command, at most ``workers`` at once, under the cap.

        Parameters
        ----------
        commands : list of list of str
            One argv per fold. The seam owns the spawn, so the cap is its
            mechanism and never something a caller re-implements.
        cwd : str or None
            Working directory for EVERY fold — a batch is one walk.
        env : dict or None
            Environment for every fold; ``None`` inherits this process's.

        Returns
        -------
        list of subprocess.CompletedProcess
            In input order, whatever order the folds finished in.

        Raises
        ------
        ValueError
            When ``commands`` is not a non-empty list of argv lists, or
            when the cap exceeds a finite hard ``RLIMIT_AS`` — raised in
            the parent before any fold starts, never wrapped into a
            result.
        RuntimeError
            From the default :meth:`spawn` when a fold exits nonzero.
            The folds not yet started are dropped; a running one is not
            killed, and there is no timeout.
        """
        _check_commands(commands)
        self._check_cap()
        if self.workers == 1:
            return self._run_serial(commands, cwd, env)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.workers)
        try:
            futures = [
                pool.submit(self.spawn, index, argv, cwd, env)
                for index, argv in enumerate(commands)
            ]
            # Indexed, not as-completed: the caller reads folds in cutoff
            # order, and the first failure in INPUT order is the one raised.
            return [future.result() for future in futures]
        finally:
            # cancel_futures drops what has not started, so a batch that
            # is going to fail does not first drain every other fold.
            # submit() itself can raise, which is why it sits inside.
            pool.shutdown(wait=True, cancel_futures=True)

    def spawn(self, index, argv, cwd, env):
        """Run one fold under the cap — the hook a subclass overrides.

        Override how ONE fold runs, never the pooling. Runs on a pool
        thread when the width is above 1, so an override must be
        thread-safe. The default wraps ``argv`` in the shim when a cap is
        set, runs it with no shell and no ``preexec_fn``, captures stdout
        and stderr together, and refuses a nonzero exit.

        Parameters
        ----------
        index : int
            The fold's position in the batch.
        argv : list of str
            The fold's own command.
        cwd : str or None
            Working directory, batch-wide.
        env : dict or None
            Environment, batch-wide.

        Returns
        -------
        subprocess.CompletedProcess
            With ``stdout`` holding both streams as text.

        Raises
        ------
        RuntimeError
            When the fold exits nonzero, carrying the tail of its output.
        """
        done = subprocess.run(
            self._capped(argv),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if done.returncode != 0:
            tail = (done.stdout or "")[-_OUTPUT_TAIL:]
            raise RuntimeError(
                f"fold {index} exited {done.returncode}; output tail:\n{tail}"
            )
        return done

    def measure_one(self, argv, cwd=None, env=None):
        """Run ONE fold and report the peak resident memory of that fold alone.

        The only way the seam reports memory, and it reports it for
        exactly one child. ``RUSAGE_CHILDREN.ru_maxrss`` is the
        high-water mark over EVERY child a process has ever reaped, so
        this process's own counter can hold an earlier child's peak. The
        fold therefore runs as the only child of a fresh measuring
        interpreter, which reads the counter it started at zero (and
        refuses to run if it did not). The one command runs at width 1
        whatever ``workers`` says, under the instance's own cap and
        through the same parent-side validation and :meth:`spawn` hook,
        which receives the fold's own ``argv``.

        Parameters
        ----------
        argv : list of str
            The one fold's command.
        cwd : str or None
            Its working directory.
        env : dict or None
            Its environment.

        Returns
        -------
        tuple
            ``(CompletedProcess, peak_rss_bytes)``; the process's
            ``stdout`` is the fold's own output.

        Raises
        ------
        ValueError
            When ``argv`` is not an argv list of strings, from the same
            parent-side cap validation :meth:`run` does, or when the
            spawn returned no reading (an override that never ran the
            measuring wrapper).
        RuntimeError
            From :meth:`spawn` when the fold exits nonzero, or when the
            measuring interpreter refuses a nonzero child counter.
        """
        _check_commands([argv])
        self._check_cap()
        nonce = secrets.token_hex(16)
        # A per-call copy carries the nonce, so the wrapper reaches the
        # default spawn through a subclass's own spawn override without
        # touching this instance, which another thread may be running.
        measuring = copy.copy(self)
        measuring._measure_nonce = nonce
        # The serial path, never the pool: a pool for one command is a
        # thread and a queue for nothing (ADR-0093).
        done = measuring._run_serial([argv], cwd, env)[0]
        output, peak = _split_reading(done.stdout, nonce)
        fold = subprocess.CompletedProcess(done.args, done.returncode, output, done.stderr)
        return fold, peak * _RU_MAXRSS_UNIT

    def _run_serial(self, commands, cwd, env):
        """Run every command one after another, building no pool."""
        return [
            self.spawn(index, argv, cwd, env) for index, argv in enumerate(commands)
        ]

    def _check_cap(self):
        """Refuse, in the parent, a cap the children could never set."""
        if self.memory_limit_bytes is None:
            return
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # RLIM_INFINITY is -1 and every default machine reports it, so an
        # unguarded comparison would refuse everywhere.
        if hard != resource.RLIM_INFINITY and self.memory_limit_bytes > hard:
            raise ValueError(
                f"memory_limit_bytes {self.memory_limit_bytes} exceeds the hard "
                f"RLIMIT_AS {hard} the folds would inherit"
            )

    def _capped(self, argv):
        """``argv`` behind the cap shim, itself when uncapped; measured when asked."""
        if self.memory_limit_bytes is None:
            command = list(argv)
        else:
            command = [
                sys.executable, "-I", "-c", _SHIM, str(self.memory_limit_bytes), *argv
            ]
        if self._measure_nonce is None:
            return command
        return [sys.executable, "-I", "-c", _MEASURE, self._measure_nonce, *command]
