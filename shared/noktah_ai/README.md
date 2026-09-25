# noktah_ai

Which AI model each case uses, for every service that calls a model. One copy, mounted
read-only into the containers that need it; nothing here is copied into an image.

| File | What |
|---|---|
| `models.yaml` | The six cases, each with its accepted models (best value first), a Bahasa label and description for the Hub, and why each model is there |
| `rotation.py` | Which model a case uses right now: the first, then the next after 3 failures in a row, back to the first after 60 minutes |

| Case | Called by | Where the cost is recorded |
|---|---|---|
| `summary` | hub-api, `app/summary/service.py` | `ai_ledger` (`hub.summary`) |
| `intake` | hub-api, `app/intake/pipeline.py`, text sources | `ai_ledger` (`hub.intake`) |
| `intake_image` | hub-api, same, screenshots and scanned PDFs | `ai_ledger` (`hub.intake`) |
| `image` | roach `/analyze`, images and carousels | `content_extractions` / `extraction_quarantine` (`media_path = image`) |
| `video` | roach `/analyze`, video | same (`media_path = video`) |
| `generation` | Prefect songbird, `tasks/openrouter_tasks.py` | `ai_usage` (migration 013) |

The Hub's Biaya AI page (`GET /v1/ai/costs`) reads those tables per case and month.

## Using it

```python
from noktah_ai import rotation

models = rotation.for_case("summary")
model = models.current()
try:
    result = call(model)
except SomeFailure:
    models.failure(model)
    raise
models.success(model)
```

A caller that must use one exact model (the calibration comparison) passes it and
reports nothing: its outcome says nothing about the case's routing.

## Where it is mounted

`docker-compose.yml` mounts `./shared/noktah_ai` at `/app/noktah_ai` in `api`, `roach`,
`prefect` and `prefect-worker`; `/app` is already on each image's `PYTHONPATH`. Outside
Docker, each service's pytest config adds `../../shared` to the path.

After editing `models.yaml`: `docker-compose restart api roach prefect prefect-worker`.
The first time the mount is added the containers must be recreated instead
(`docker-compose up -d api roach prefect prefect-worker`).
