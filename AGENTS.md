# RepoWayfinder Agent

This is the AI-facing variant, derived from public RepoWayfinder commit
be3e5a060b178d42178681b854282de5e46b2d87. The original project is separate.

Use `repo_wayfinder` alongside the host's native tools. Keep repository setup
(fixed source, project environment, installation, execution evidence) in a job.
For task-specific authoring, transformations or inspection, use native tools
when simpler; do not force every stage through files/checks/replan. A setup-only
success proves setup, not the final deliverable. Use the returned project_path
and result.python_runtime.executable for native continuation, keeping writes in the task.

For an existing task directory, `rw_verify` batches explicit CSV/JSON/file
assertions or owned loopback HTTP request checks. It does not acquire source,
install dependencies or create a deployment job. Optional `run` executes argv;
`unchanged` explicitly runs it twice and compares parsed artifacts. A service
scenario owns its process lifetime and stops it when checks finish. Supply the
user's actual expected results and failure cases; passing only proves those
assertions. Prefer existing native tools for work outside this finite contract.
Select this path when it removes repeated parsing, execution or service lifecycle
code. Keep compact native checks; rewriting them as an equally long assertion
manifest is not an efficiency gain. Reuse still-valid evidence and check only
changed behavior or uncovered risks instead of duplicating passing assertions.
See `docs/LOCAL_VERIFICATION.md`. Product comparisons must work with the host's
ordinary instructions plus the product's shipped capability guidance. Reusable,
de-identified prompts or Skills may be part of that delivery; record them as
part of the product arm. Do not rely on undistributed private rules, hidden
benchmark-only guidance or disabled native abilities.

When using the service's batched execution path:

1. `rw_search` only if a repository has not been chosen.
2. For an authorized finite task, search exactly `rw_run` first (limit 1),
   then pass `repository` and `commands`; revision and checks are optional.
   Pass `python_version` (such as `3.11`) when the task selects a version.
   Python/pip steps create/reuse the job venv automatically. Start with package
   installation or task commands; do not bootstrap or activate another venv.
   It acquires, validates, executes and checks output in one call, waiting up to
   50 seconds or returning sooner on completion. Do not write action/type/purpose/reason boilerplate.
   `rw_prepare` obtains source and a plan without running target code. Give an
   explicit plan when the deterministic route is insufficient. Keep one command
   per step. Include result checks for the user's actual requested output.
   When task inputs are needed, include `files: [{path, content}]` in `rw_run`
   for configuration, documents and input directly. These are new UTF-8 files
   relative to the checkout; existing files are never overwritten. Batch known
   steps and use short scripts for necessary glue. Do not embed all inputs or
   duplicate service checks in a giant script. Installed executable names need no product allowlist; use an explicit
   shell for builtins and cmdlets.
   In Python scripts, launch Python children with `sys.executable` (pip with
   `sys.executable, '-m', 'pip'`), never a bare `python` name. Other child CLIs
   should use `shutil.which`. Shell-only plans without python_version do not create a Python venv.
3. When using the prepare route, inspect the returned plan and
   blocked/configuration information, then call `rw_execute`. Do not execute
   again after a successful `rw_run`. The call authorizes that plan, not
   arbitrary future actions.
4. `rw_status(wait_seconds=30)` waits without flooding context. `rw_replan`
   reuses a stopped job's checkout and environment. Supply exact
   `edits: [{path, old, new}]` for untracked task files, optional new `files`,
   and `commands` for only affected steps. Set `execute: true` to revalidate and
   run in the same call. Otherwise it stops prepared. Omitted commands/checks
   reuse the saved values. Checks default to output from this execution. For
   intentionally retained earlier artifacts, use `freshness: "preserved"`;
   this requires prior verification in this job and identical digest, keeping
   its original producing attempt. Unverified old files cannot pass. A recovery
   `request_id` prevents duplicate edits/execution on retransmission. Reconcile
   partial effects before retrying. Prior inputs and execution evidence remain.
   Read `rw_logs`
   only when the compact result is insufficient. Do not repeatedly fetch source
   or logs that are already represented in a valid job.
5. Waiting for prerequisites and security blocks are not success. After the
   cause is resolved, `rw_resume` explicitly reruns the saved plan. Reconcile
   prior effects first; commands are not generally transactional.

Result checks cover only the stated assertions. HTTP availability is not proof
of application-task completion. Output-file checks accept current output or
explicitly preserved previously verified job artifacts; arbitrary pre-existing files do
not establish task success. Runtime validation servers
are stopped after probing, so never advertise their URL as a running service.

No nested AI requests, interactive novice menus, global prerequisite installers,
host skill installation, or automatic browser/report opening are part of this
entry point. The protected execution checks are not an OS sandbox.

For development, keep stdout machine-readable and put diagnostics in job logs.
Preserve waiting, failure and cancellation distinctions. Keep work in the intended checkout and preserve unrelated local changes.
The beginner product is maintained separately. The public tree is the Agent
distribution; do not reintroduce novice menus or global installers.

Before model comparisons, exercise changed execution boundaries locally through
real stdio and worker processes; do not use model calls to discover basic runtime
defects. Then run the tool group as a preflight and repair
material execution/contract failures. Once the task path is stable, freeze
the candidate and run the native control with the same task and model. Do
not run a new native baseline for each known-broken tool candidate. Keep all
preflight failures, model calls, elapsed time and token usage in a separate
development ledger; retain every formal comparison attempt as well. Never
present only the fastest or successful retry as the full cost.
Companion comparisons give both groups the same native tools and permission
mode; the treatment adds RepoWayfinder. Pin the same Python version and record
the actual interpreter. Check the final artifacts independently, audit task
commands and compare before/after base-environment package inventories. A clean
inventory alone is not proof that no temporary external writes occurred.
