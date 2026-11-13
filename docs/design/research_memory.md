# Research memory

The Alpha research memory is a layered system for carrying campaign results,
validation evidence and hard negatives into the next research decision.

## 1. Structured facts

Campaign facts come from `campaign.json`, `rounds/`, `team/result.json` and
FactorBuilder artifacts. Validation facts are appended to
`output/factor_lab/alpha/memory/validation_results.jsonl` with this schema:

```json
{
  "schema": "factor_lab.alpha.validation_record.v1",
  "validation_id": "VAL-...",
  "campaign_id": "CAMP-...",
  "factor_id": "iter...",
  "status": "pass | fail | unverified",
  "tests": {
    "independent": {"status": "fail", "alpha_t": 0.69},
    "fdr": {"status": "fail", "p": 0.001, "threshold": 0.0006},
    "topn": {"status": "warn"},
    "capacity": {"status": "unverified"}
  },
  "source_paths": [],
  "data_hashes": {},
  "result_hash": "...",
  "created_at": "..."
}
```

`status=fail` when any test fails; `unverified` when no test passes and none
fails. The ledger is append-only; `latest_validations()` keeps the latest
record per campaign.

## 2. Context builder

`ResearchMemory.build_context(parent_campaign_ids)` builds
`factor_lab.alpha.research_context.v2` containing:

- `source_hashes`: hash of every parent campaign result;
- `hard_constraints.hard_negatives`: every failed/unverified validation;
- `prior_results`: deterministic compact metrics;
- `compressed_history`: facts after budget compression;
- `compression`: mode, budget and source count;
- `duplicates`: exact expression matches in the parent set;
- `open_questions`.

Every parent result hash is verified by `validate_context()`. Where available,
the context also records the parent `data_manifest_hash`, `data_hash`,
`price_basis`, `evaluation_window` and `factor_code_hash`; these are checked on
validation. A context that omits a hard negative or references a stale/missing
result or stale data provenance fails validation.

`CampaignController.create(..., prior_campaign_ids=[...])` writes this context
to `team/research_context.json` automatically and records
`parent_campaign_ids` in `campaign.json`.

## 3. Compression

The builder has a deterministic fallback and an injected LLM hook:

```python
context = memory.build_context(
    parents,
    budget_chars=12000,
    compressor=lambda payload: {
        "source_campaign_ids": [...],
        "summary": "...",
    },
)
```

An injected compressor must return `source_campaign_ids` covering every parent
campaign; otherwise context building fails. The deterministic fallback keeps
all hard negatives and one latest fact per factor. LLM compression is optional;
the deterministic facts remain the source of truth. A DeepSeek Flash adapter is available in
``factor_lab.alpha.llm_compressor.DeepSeekCompressor``. It reads the key from
the gitignored ``config.json`` (``llm.deepseek.api_key``) or
``DEEPSEEK_API_KEY`` and validates that every source campaign is present in the
returned JSON. The deterministic fallback is used when no compressor is
supplied.

## 4. Audit

Research memory is required for campaigns created at or after
`campaign_protocol.research_memory_policy.required_from`. Campaigns before that
boundary remain facts but are outside memory enforcement. `protocol_audit.audit_campaign()`
checks every required campaign:

- `team/research_context.json` must exist;
- schema must be `research_context.v2`;
- every source hash must match the current parent result;
- every hard negative from the ledger must appear in the context;
- every parent data provenance hash must still match;
- lineage must not contain cycles or missing parents.

The audit is deterministic and does not call an LLM.

## 5. DeepSeek Flash

```json
"llm": {
  "deepseek": {
    "api_key": "<gitignored config.json>",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "timeout_seconds": 120,
    "max_retries": 2
  }
}
```

The adapter is called only when a compressor is injected:

```python
from factor_lab.alpha.llm_compressor import DeepSeekCompressor

context = memory.build_context(parents, budget_chars=3000, compressor=DeepSeekCompressor())
```

A response that omits a source campaign is rejected after one strict retry.

## 6. CLI

```bash
python -m factor_lab alpha memory
python -m factor_lab alpha memory --memory-action record-validation --validation-file validation.json
python -m factor_lab alpha memory --memory-action build-context --campaign-id CAMP-... --parent-campaign-id CAMP-... --context-budget 12000 --llm-compress
```
