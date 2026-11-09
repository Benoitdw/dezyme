# Analysis lifecycle

What happens to an analysis from the moment the user clicks **Run** to the
moment the results are shown: which component does what, where the files go, and
how the status changes.

See also [adding-a-software.md](adding-a-software.md).

---

## The actors

| Actor | Where | Role |
|-------|-------|------|
| Run page | `frontend/src/routes/run/+page.svelte` | Builds the payload, `POST /api/analysis` |
| Results page | `frontend/src/routes/results/[id]/+page.svelte` | Polls the status, shows the log, then the results |
| API routes | `backend/app/routes/analysis.py` | Submit, status, payload, logs, results, files |
| Submit handler | `backend/app/submit/<tool>.py` | Validates, writes the inputs, inserts the DB rows |
| Watcher | `backend/app/watchers/base.py` | One thread per software, ticks every 5 s, runs the queue and drives the status |
| Job class | `backend/app/jobs/<tool>.py` | Turns the stored inputs into command-line arguments |
| Runner | `backend/app/runner.py` | HTTP client of the wrapper |
| Wrapper | `wrapper/api.py`, in the software container | Runs the command at once, writes `status.json` |
| SQLite | `backend/data/analyses.db` | `analysis` table + one `<tool>_input` table per software |
| Job folder | `data/jobs/<id>/` (mounted at `/data/jobs/<id>`) | Inputs, outputs, log, wrapper state |

---

## The big picture

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant R as Run page
    participant API as Backend API
    participant DB as SQLite
    participant FS as data/jobs/<id>
    participant WT as Watcher (thread)
    participant WR as Wrapper (software container)
    participant RP as Results page

    U->>R: fill the form, Run
    R->>API: POST /api/analysis {tool, structureId, chains, pdbContent, msaContent, params}
    API->>API: validate (pydantic + submit handler)
    API->>FS: input/input.pdb, input/<msa>
    API->>DB: analysis(status=waiting) + <tool>_input
    API-->>R: 201 {analysis_id}
    R->>R: save id in localStorage (recent jobs)
    R->>RP: goto /results/<id>

    loop every 3 s
        RP->>API: GET /status
        API->>DB: read status
        API-->>RP: waiting (+ queue_position) | running | done | error
    end

    Note over WT: tick every 5 s
    WT->>DB: oldest status=waiting, if fewer than max_parallel running
    WT->>FS: prepare_inputs() (extra input files)
    WT->>WR: POST /run/<id> {args}
    WR->>FS: status.json = running
    WR-->>WT: 202
    WT->>DB: status=running

    WR->>WR: run RUN_CMD + args
    WR->>FS: job.log, output/*, status.json = done/failed

    loop every 2 s while running
        RP->>API: GET /logs
        API->>FS: output/*_logs.txt or job.log
    end

    Note over WT: next ticks
    WT->>DB: analyses with status=running
    WT->>WR: GET /status/<id>
    WR-->>WT: {state: done}
    WT->>DB: status=completed

    RP->>API: GET /status → done
    RP->>API: GET /results
    API-->>RP: URLs of the result files
    RP->>API: GET /output/<file> (CSV, JSON, PDB...)
    RP->>U: results page
```

---

## 1. Creation (`POST /api/analysis`)

### Frontend

`handleSubmit()` in the run page builds the payload:

```json
{
  "tool": "popmusic",
  "structureId": "6acv",
  "pdbFilename": "6acv",
  "chains": ["A"],
  "pdbContent": "ATOM ...",
  "msaContent": ">seq ...",
  "msaFilename": "6acv_A.a3m",
  "params": { "multipleMutations": ["AA14R:HA17L"], "mutationMsaReference": false }
}
```

`params` holds the tool's form fields (by `name`), `mutations` (custom mutation
list) and `multipleMutations` when the tool supports them.

### Backend

```mermaid
flowchart TD
    A["POST /api/analysis"] --> B{"SubmitPayload<br/>(pydantic)"}
    B -- "bad chain id / filename" --> E422["422"]
    B --> C{"make_handler(tool)<br/>SUBMIT_REGISTRY"}
    C -- unknown tool --> E422
    C --> D{"handler.handle()<br/>tool-specific checks"}
    D -- "missing PDB/MSA,<br/>wrong number of chains,<br/>bad mutations..." --> E422
    D --> F{"create_analysis()<br/>tool in JOB_REGISTRY<br/>and in config.toml?"}
    F -- no --> E422b["422 'not available yet'"]
    F --> G["id = uuid4()"]
    G --> H["write data/jobs/&lt;id&gt;/input/<br/>input.pdb, &lt;msa&gt;"]
    H --> I["INSERT analysis (status=waiting)<br/>INSERT &lt;tool&gt;_input"]
    I --> J["201 {analysis_id}"]
```

- Validation happens **here**, synchronously: the user gets the error on the form.
- Only the files sent by the user are written now. Files derived from the
  parameters (e.g. PoPMuSiC's `multiple_mutations.txt`) are written later by the
  job class.
- Nothing is sent to the software yet: the request returns as soon as the rows
  are committed.

At this point the job folder looks like:

```
data/jobs/<id>/
└── input/
    ├── input.pdb
    └── 6acv_A.a3m
```

---

## 2. Statuses

The backend stores 4 statuses in `analysis.status`. The API maps them to the
4 statuses the frontend knows:

| DB (`analysis.status`) | API / frontend | Meaning |
|------------------------|----------------|---------|
| `waiting` | `waiting` | In the backend's queue, not sent to the software yet |
| `running` | `running` | Sent to the wrapper, which started it at once |
| `completed` | `done` | The tool exited with code 0 |
| `failed` | `error` | See `failure_reason` |

```mermaid
stateDiagram-v2
    [*] --> waiting: POST /api/analysis
    waiting --> waiting: max_parallel already running,<br/>or service unavailable (retry next tick)
    waiting --> running: free slot, POST /run accepted<br/>(202 or 409)
    waiting --> failed: launch_error
    running --> running: wrapper says running
    running --> completed: wrapper says done<br/>(exit code 0)
    running --> failed: job_error<br/>(exit code ≠ 0)
    running --> failed: job_dead<br/>(lost or unknown)
    completed --> [*]
    failed --> [*]
```

`failure_reason` (returned as `error` by `/status`):

| Reason | When | Typical cause |
|--------|------|---------------|
| `launch_error` | Exception while preparing or submitting the job | Input record missing, `prepare_inputs`/`build_args` crashed, wrapper answered 404/422 (job folder missing, invalid arg) |
| `job_error` | The tool exited with a non-zero code | Bad input the tool rejected, tool crash. See the log. |
| `job_dead` | Wrapper reports `lost`, or doesn't know the job (404) | The software container was restarted/rebuilt while the job was running |

`failed` and `completed` are final: nothing restarts a failed analysis; the
user submits a new one.

### The queue

The queue is the database. Each software runs at most `max_parallel` analyses
at the same time (`[<software>]` section of `config.toml`, default 1); the
others stay `waiting` and start oldest first (`created_at`). Each software has
its own queue: a waiting HotMuSiC analysis never waits for PoPMuSiC.

While an analysis waits, `GET /status` also returns its `queue_position`:
1 + the analyses of the same software that wait since before it. The results
page shows it ("In the queue: position 2"); it becomes `null` once running.

---

## 3. The watcher

At startup (`lifespan` in `app/main.py`), `start_watchers()` starts one
`SoftwareWatcher` daemon thread per software that is in `JOB_REGISTRY` **and**
has a `url` in `config.toml`. Every 5 seconds each one runs a tick, with its own
DB session. It checks the running analyses first, so a job that just ended
frees its slot in the same tick:

```mermaid
flowchart TD
    T(["tick (every 5 s)"]) --> R["_tick_running:<br/>analyses with status=running"]
    R --> M{"for each"}
    M --> G["runner.get_state()<br/>GET /status/&lt;id&gt;"]
    G -- running --> M
    G -- done --> C["status = completed"]
    G -- failed --> F1["status = failed<br/>job_error"]
    G -- "lost / 404" --> F2["status = failed<br/>job_dead"]
    G -- "service unreachable" --> STOP2["stop, retry next tick"]
    G -- "other error" --> M
    C --> M
    F1 --> M
    F2 --> M

    M -- done --> W["_tick_waiting:<br/>free = max_parallel − running<br/>waiting analyses, oldest first"]
    STOP2 --> W
    W --> L{"free > 0?"}
    L -- no --> END(["wait for next tick"])
    L -- yes --> P["job = JobClass(config)<br/>prepare_inputs()<br/>args = build_args()"]
    P --> S["runner.submit(url, id, args)<br/>POST /run/&lt;id&gt;"]
    S -- "202 / 409" --> SR["status = running<br/>free − 1"]
    S -- "service unreachable" --> STOP1["stop, all stay waiting"]
    S -- "other error / exception" --> SF["status = failed<br/>launch_error"]
    SR --> L
    SF --> L
```

Properties worth knowing:

- **The service being down is not an error.** If the wrapper does not answer
  (container stopped, rebuilding, downloading its weights on first start), the
  analyses stay `waiting` (or `running`) and the next tick tries again. Users just
  wait longer.
- **No double run.** The wrapper refuses a job that already has a
  `status.json` (`409`), and the runner treats `409` as success. If the backend
  crashed between the `POST /run` and the `status = running` commit, the next
  tick re-submits and simply moves it to `running`.
- **Backend restarts are harmless.** The queue is in SQLite and the run state
  in `status.json`; the watcher picks up where it left off.
- **Software container restarts are not**: what was running there is
  lost and ends as `job_dead`.
- Worst-case latency: up to 5 s before a free slot is used, and up to 5 s
  after the tool ends before the status becomes `completed`. The frontend polls
  every 3 s on top of that.

---

## 4. Execution in the software container

The wrapper receives `POST /run/<id>` with the args built by the job class,
e.g. for PoPMuSiC:

```
popmusicevol /data/jobs/<id>/input/6acv_A.a3m /data/jobs/<id>/input/input.pdb A \
  --output_dir /data/jobs/<id>/output --run_name 6acv_A \
  --multiple_mutations_file /data/jobs/<id>/input/multiple_mutations.txt \
  --disable_log_progress --allow_invalid_mutations
```

It writes `status.json = running`, returns `202` and runs the command at once in
a thread, with the job folder as working directory. The wrapper has no queue:
the backend never sends more than `max_parallel` jobs. stdout and stderr go to `job.log`. When the process exits,
`status.json` becomes `done` (exit code 0) or `failed`.

```json
{
  "state": "done",
  "argv": ["popmusicevol", "/data/jobs/<id>/input/6acv_A.a3m", "..."],
  "started_at": "2026-11-06T09:41:02+00:00",
  "returncode": 0,
  "finished_at": "2026-11-06T09:51:37+00:00"
}
```

The job folder at the end:

```
data/jobs/<id>/
├── input/                      written by the backend
│   ├── input.pdb
│   ├── 6acv_A.a3m
│   └── multiple_mutations.txt  (prepare_inputs)
├── output/                     written by the tool
│   ├── 6acv_A_mutations.csv
│   ├── 6acv_A_multiple_mutations.csv
│   ├── 6acv_A_metadata.json
│   ├── 6acv_A_logs.txt
│   └── ...
├── job.log                     stdout + stderr (wrapper)
├── status.json                 wrapper state
└── output.zip                  built on the first download
```

---

## 5. The results page

`/results/<id>` works for every state and can be reloaded or shared at any time:
everything it shows comes from the API, nothing from the submit step (the recent
jobs list in `localStorage` only holds ids, for the home page).

```mermaid
flowchart TD
    O(["onMount"]) --> P["GET /analysis/&lt;id&gt;<br/>(payload summary: tool, structure, params, file sizes)"]
    O --> S["poll GET /status every 3 s"]
    S -- 404 --> NF["'Analysis not found'"]
    S -- waiting --> PE["'In the queue: position N'<br/>(queue_position)"]
    S -- running --> RU["'Running…'<br/>+ poll GET /logs every 2 s"]
    S -- error --> ER["'Analysis failed' + reason<br/>+ final log, polling stops"]
    S -- done --> D["polling stops<br/>GET /results → file URLs"]
    D --> F["GET /output/&lt;file&gt; for each file<br/>parse CSV / JSON / PDB"]
    F --> V["tool results component<br/>(PopmusicEvolResults, HotmusicResults)"]
```

Endpoints used:

| Endpoint | Returns |
|----------|---------|
| `GET /api/analysis/{id}` | Summary of what was submitted (no file contents) |
| `GET /api/analysis/{id}/status` | `{status, error, queue_position}` |
| `GET /api/analysis/{id}/logs` | `output/*_logs.txt` if the tool wrote one, else `job.log`, else empty |
| `GET /api/analysis/{id}/results` | URLs of the files the results page needs (`409` until `completed`) |
| `GET /api/analysis/{id}/output` | Names of the files in `output/` |
| `GET /api/analysis/{id}/output/{file}` | A file from `output/` or `input/` (output wins) |
| `GET /api/analysis/{id}/msa` | The submitted MSA |
| `GET /api/analysis/{id}/download` | Zip: inputs at the root, results under `output/` (`409` until `completed`) |

---

## Debugging an analysis

| Symptom | Look at |
|---------|---------|
| Stays *In the queue* | Normal while `max_parallel` analyses of that software run. If nothing runs: `docker compose ps` / `logs <software>`, the service is down or still starting (backend logs: `service unavailable, analysis … keeps waiting`). Also check that the software has a `[section]` in `config.toml` (else no watcher: `watcher not started` at startup). |
| *Running…* for a long time | The tool is working: see `job.log` and `output/*_logs.txt`. |
| `launch_error` | Backend logs: `Failed to launch analysis <id>: …`. |
| `job_error` | `data/jobs/<id>/job.log` and `status.json` (`argv`, `returncode`). Re-run the `argv` by hand in the container. |
| `job_dead` | The software container was restarted while the job ran (deploy, crash, OOM: `docker compose ps`, `dmesg`). Resubmit. |
| `done` but no results shown | `/results` didn't find the expected files: compare `ls data/jobs/<id>/output` with what the endpoint looks for. |

Inspect the DB:

```bash
sqlite3 backend/data/analyses.db \
  "select id, software, status, failure_reason, created_at, updated_at
   from analysis order by created_at desc limit 10;"
```
