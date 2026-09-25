"""
Extraction vocabulary tasks (feature 007-structured-extraction).

The versioned closed vocabulary that beat functions — and, when S-05 lands,
attribute dimensions — are drawn from (FR-004, FR-043a).

The authoritative source is `config/extraction/vocabulary_v1.yaml`, a
version-controlled file reconciled into `extraction_vocabulary_terms` by
`extraction.vocabulary.sync`. Same relationship, and the same reasoning, as
`config/field_availability.yaml` has with `field_availability` in feature 005:
the file is the write path and reviewable as a diff, the table is the read path
that lets SC-002 ("every beat function is valid under the vocabulary version its
extraction recorded") be a foreign key rather than an application walk.

TWO invariants here are load-bearing and neither is expressible as a row
constraint:

  * A version that any extraction has recorded is FROZEN. The sync must fail
    loudly rather than alter it — silently mutating a recorded version makes
    FR-030 retroactively false for every row already stored.
  * Every dimension MUST publish exactly one residual term. The partial unique
    index bounds this from above only; nothing in the schema requires one to
    exist, and FR-003 ("no discernible structure is recorded as a single beat
    carrying the residual function") is unsatisfiable without it.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from prefect import task

try:
    from ..db import db_pool, maybe_transaction
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool, maybe_transaction

logger = logging.getLogger(__name__)


async def _db_pool(db_env: Optional[str] = None):
    """The module-local pool seam every task module here exposes.

    Tests monkeypatch `_db_pool`, not `db_pool` — see availability_tasks.py for
    why patching the shared helper directly stopped catching arity mistakes.
    """
    return await db_pool(db_env)


# Resolved by probing, for the same reason availability_tasks does it: the repo
# layout and the container layout put this file at different depths. parents[1]
# is /app in the container; parents[3] is the repo root on the host.
_HERE = Path(__file__).resolve()
_CANDIDATE_SOURCE_PATHS = tuple(
    base / "config" / "extraction" / "vocabulary_v1.yaml"
    for base in (_HERE.parents[1], *_HERE.parents[3:4])
)

# The dimension this feature owns. S-05 adds its own dimensions to the same file
# and the same table — that is FR-043a, and the reason this constant is a floor
# rather than the whole vocabulary.
BEAT_FUNCTION_DIMENSION = "beat_function"


def _default_source_path() -> Path:
    return next((c for c in _CANDIDATE_SOURCE_PATHS if c.exists()), _CANDIDATE_SOURCE_PATHS[0])


def _source_path(path: Optional[str] = None) -> Path:
    explicit = path or os.environ.get("EXTRACTION_VOCABULARY_PATH")
    return Path(explicit) if explicit else _default_source_path()


class VocabularySourceError(ValueError):
    """The YAML source is malformed. Raised before anything is written."""


class FrozenVocabularyError(RuntimeError):
    """An attempt to alter a version that extractions have already recorded.

    Not a warning and not a partial apply. Once a `content_extractions` row cites
    a version, every stored value under it must stay interpretable against the
    membership it was assigned under (FR-030). Editing that membership in place
    would silently rewrite the meaning of history; the correct move is a NEW
    version.
    """


def load_vocabulary(path: Optional[str] = None) -> tuple[str, List[Dict[str, Any]]]:
    """
    Parse and validate the YAML source. Pure — no I/O beyond the read.

    Returns (version, rows) where each row is one term ready to upsert.
    Raises VocabularySourceError on anything malformed, having written nothing:
    importing the valid subset would let a term silently revert to a stale
    description, which still reads as authoritative to the prompt builder.
    """
    source = _source_path(path)
    if not source.exists():
        raise VocabularySourceError(f"vocabulary source not found: {source}")

    try:
        doc = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise VocabularySourceError(f"{source}: invalid YAML: {e}") from e

    version = str(doc.get("version") or "").strip()
    if not version:
        raise VocabularySourceError(f"{source}: missing top-level 'version'")

    dimensions = doc.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise VocabularySourceError(f"{source}: 'dimensions' must be a non-empty list")

    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for block in dimensions:
        if not isinstance(block, dict):
            raise VocabularySourceError(f"{source}: each dimension must be a mapping")
        dimension = str(block.get("dimension") or "").strip()
        if not dimension:
            raise VocabularySourceError(f"{source}: a dimension block is missing 'dimension'")

        terms = block.get("terms")
        if not isinstance(terms, list) or not terms:
            raise VocabularySourceError(f"{source}: dimension '{dimension}' has no terms")

        residuals = 0
        for entry in terms:
            if not isinstance(entry, dict):
                raise VocabularySourceError(
                    f"{source}: dimension '{dimension}' has a term that is not a mapping")
            term = str(entry.get("term") or "").strip()
            if not term:
                raise VocabularySourceError(f"{source}: dimension '{dimension}' has a term with no name")
            description = str(entry.get("description") or "").strip()
            if not description:
                # The description is what the prompt is built from. A blank one
                # would silently weaken the instruction the model is given.
                raise VocabularySourceError(
                    f"{source}: term '{dimension}.{term}' has no description; "
                    f"the prompt is built from it")

            key = (dimension, term)
            if key in seen:
                raise VocabularySourceError(f"{source}: duplicate term '{dimension}.{term}'")
            seen.add(key)

            is_residual = bool(entry.get("is_residual", False))
            residuals += 1 if is_residual else 0

            ordinal = entry.get("ordinal")
            rows.append({
                "version": version,
                "dimension": dimension,
                "term": term,
                "is_residual": is_residual,
                "ordinal": int(ordinal) if ordinal is not None else None,
                "description": description,
            })

        # A MISSING residual is only an error for `beat_function`, and the
        # asymmetry is not an oversight. `beats` has minItems: 1, so the model is
        # REQUIRED to emit a beat for every item and therefore needs a legal way
        # to say "no discernible structure" (FR-003) — without a residual there is
        # no such value and the model must either invent a term or misapply one.
        #
        # Attribute dimensions carry no such obligation: `attributes` may be
        # empty and holds at most one value per dimension, so "this dimension does
        # not apply here" is expressed by OMITTING it. Requiring a residual on
        # every dimension would impose a term on S-05 that its vocabulary may have
        # no use for — a constraint this feature has no standing to add, given
        # FR-043a promises S-05 a mechanism it can populate as-is.
        #
        # A SECOND residual is always an error, and that half the database does
        # catch (uq_vocabulary_residual). Zero is the half nothing else catches.
        if residuals == 0 and dimension == BEAT_FUNCTION_DIMENSION:
            raise VocabularySourceError(
                f"{source}: dimension '{dimension}' publishes no residual term. "
                f"FR-003 requires content with no discernible structure be recorded as one beat "
                f"carrying the residual function; without one there is no legal value for it.")
        if residuals > 1:
            raise VocabularySourceError(
                f"{source}: dimension '{dimension}' publishes {residuals} residual terms; "
                f"exactly one is allowed")

    return version, rows


@task(name="extraction.vocabulary.sync", retries=0)
async def extraction_vocabulary_sync(
    path: Optional[str] = None, validate_only: bool = False, db_env: Optional[str] = None
) -> Dict[str, Any]:
    """
    Reconcile the YAML source into `extraction_vocabulary_terms`.

    Refuses outright — writing nothing — when the version being synced is frozen
    and its membership or descriptions would change. An unchanged re-sync of a
    frozen version is a no-op and succeeds, so the flow stays safe to run on a
    schedule.
    """
    version, rows = load_vocabulary(path)

    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            frozen_at = await conn.fetchval(
                "SELECT max(frozen_at) FROM extraction_vocabulary_terms WHERE version = $1",
                version,
            )

            existing = {
                (r["dimension"], r["term"]): r
                for r in await conn.fetch(
                    "SELECT dimension, term, is_residual, ordinal, description "
                    "FROM extraction_vocabulary_terms WHERE version = $1",
                    version,
                )
            }

            incoming = {(r["dimension"], r["term"]): r for r in rows}
            added = sorted(set(incoming) - set(existing))
            removed = sorted(set(existing) - set(incoming))
            changed = sorted(
                k for k in set(incoming) & set(existing)
                if (incoming[k]["description"] != existing[k]["description"]
                    or incoming[k]["is_residual"] != existing[k]["is_residual"]
                    or incoming[k]["ordinal"] != existing[k]["ordinal"])
            )

            if frozen_at is not None and (added or removed or changed):
                raise FrozenVocabularyError(
                    f"vocabulary version '{version}' was frozen at {frozen_at} because extractions "
                    f"have recorded it, and this sync would change it "
                    f"(added={added} removed={removed} changed={changed}). "
                    f"Publish a NEW version instead — altering a recorded one makes every stored "
                    f"value under it uninterpretable against the membership it was assigned under "
                    f"(FR-030). Nothing was written."
                )

            inserted = updated = 0
            async with maybe_transaction(conn, validate_only):
                for row in rows:
                    status = await conn.fetchval(
                        """
                        INSERT INTO extraction_vocabulary_terms
                            (version, dimension, term, is_residual, ordinal, description, synced_at)
                        VALUES ($1, $2, $3, $4, $5, $6, now())
                        ON CONFLICT (version, dimension, term) DO UPDATE
                           SET is_residual = EXCLUDED.is_residual,
                               ordinal     = EXCLUDED.ordinal,
                               description = EXCLUDED.description,
                               synced_at   = now()
                        RETURNING CASE WHEN xmax = 0 THEN 'inserted' ELSE 'updated' END
                        """,
                        row["version"], row["dimension"], row["term"],
                        row["is_residual"], row["ordinal"], row["description"],
                    )
                    if status == "inserted":
                        inserted += 1
                    elif changed and (row["dimension"], row["term"]) in set(changed):
                        updated += 1

                # Terms withdrawn from the file are NOT deleted. A beat row may
                # reference one through fk_beat_function, and deleting it would
                # either fail the FK or (worse, if nothing referenced it yet)
                # make the version's membership differ from what a future reader
                # reconstructs. Withdrawal is a new version, like every other
                # membership change.
                if removed:
                    logger.warning(
                        "vocabulary %s: %d term(s) absent from the source but retained in the "
                        "table (%s) — withdrawal requires a new version, not a delete",
                        version, len(removed), removed,
                    )

        return {
            "version": version,
            "rows": rows,
            "inserted": inserted,
            "updated": updated,
            "unchanged": len(rows) - inserted - updated,
            "retained_not_in_source": [f"{d}.{t}" for d, t in removed],
            "frozen_at": frozen_at.isoformat() if frozen_at else None,
        }
    finally:
        await pool.close()


@task(name="extraction.vocabulary.get", retries=0)
async def extraction_vocabulary_get(
    version: Optional[str] = None,
    dimension: str = BEAT_FUNCTION_DIMENSION,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read one dimension's terms at a version, ordered.

    `version=None` means "the current version" — the highest in the table.
    Derived rather than hardcoded so publishing v2 moves every consumer without
    a code change.

    Returns {version, dimension, terms[], residual}. `terms` carries the
    descriptions the prompt is built from, which is why the prompt and the
    validator cannot disagree about what a term means.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            resolved = version or await conn.fetchval(
                "SELECT max(version) FROM extraction_vocabulary_terms")
            if not resolved:
                return {"version": None, "dimension": dimension, "terms": [], "residual": None}

            rows = await conn.fetch(
                "SELECT term, is_residual, ordinal, description "
                "FROM extraction_vocabulary_terms "
                "WHERE version = $1 AND dimension = $2 "
                "ORDER BY ordinal NULLS LAST, term",
                resolved, dimension,
            )
            terms = [dict(r) for r in rows]
            residual = next((t["term"] for t in terms if t["is_residual"]), None)
            return {
                "version": resolved,
                "dimension": dimension,
                "terms": terms,
                "residual": residual,
            }
    finally:
        await pool.close()


@task(name="extraction.vocabulary.freeze", retries=0)
async def extraction_vocabulary_freeze(
    version: str, db_env: Optional[str] = None
) -> int:
    """
    Latch a version as immutable, once an extraction has recorded it.

    Idempotent: already-frozen rows keep their original timestamp, so the freeze
    instant records when the version first became load-bearing rather than when
    this last ran. Called by the extraction store on first write at a version —
    a version nothing references yet stays editable, which is what makes
    iterating on v1's descriptions possible before it goes live.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "WITH frozen AS ("
                "  UPDATE extraction_vocabulary_terms SET frozen_at = now() "
                "  WHERE version = $1 AND frozen_at IS NULL RETURNING 1) "
                "SELECT count(*) FROM frozen",
                version,
            ) or 0
    finally:
        await pool.close()
