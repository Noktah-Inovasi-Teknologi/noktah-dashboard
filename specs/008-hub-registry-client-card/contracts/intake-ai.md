# Contract: Intake and Summary AI calls (spec 008)

Both calls go through OpenRouter, with the model set by configuration (`HUB_INTAKE_MODEL`, `HUB_SUMMARY_MODEL`; default `xiaomi/mimo-v2.5`). Every call:
- is attributed with `X-Title: Noktah Hub` and `call_site`/`client` in the ledger
- requests `usage: {include: true}` and stores `usage.cost` in `ai_ledger`
- checks `finish_reason == "length"` **before** parsing (a truncated array is still valid JSON)
- is validated against the schema below and retried **once**, with the validator's error quoted, then marked failed

## Intake (`call_site = hub.intake` / `hub.notes`)

**System prompt rules** (Bahasa, versioned as `intake_v1`):
1. Extract only what is in the input. **Never translate**; copy values **word for word**.
2. Each item names its target:
   - a Profil field key
   - a Guideline section key
   - `request`: something the Client asked Noktah to do
3. `excerpt` must be copied exactly from the input: the shortest span that supports the item.
4. Include `speaker` and `spoke_at` when the input shows them (WhatsApp lines do).
5. A price line must carry `satuan` and `syarat`. If they're absent, leave them empty; don't invent them.
6. Mark anything about an individual patient or customer (names, diagnoses, faces) as `is_patient_data: true`.
7. Greetings, scheduling chatter and thanks are not items.
8. Old notes (monthly performance numbers, meeting logistics) that fit no field: return them in `no_card_home`.

**Input message**:
- card definition v1 (field keys and one-line meaning of each)
- the Client's name
- its current Card values (Profil and Guideline) and PIC name
- the always-banned list
- the source: text, or up to 10 images

**Response schema** (strict JSON; the root must be an object):

```json
{
  "items": [
    {
      "target": "profil | guideline | request",
      "field_key": "harga_promo | ... | null for request",
      "value": "string or object matching the field's shape",
      "excerpt": "exact quote from the input",
      "speaker": "string | null",
      "spoke_at": "YYYY-MM-DD | null",
      "valid_until": "YYYY-MM-DD | null",
      "is_update": true,
      "is_patient_data": false
    }
  ],
  "no_card_home": ["short description of content that fits no field"],
  "nothing_found": false
}
```

**Post-validation (deterministic, research R3):**
- `is_patient_data` items are dropped.
- Flags are added by the rules: `unverified`, `from_image`, `not_pic`, `price_incomplete`, `other_client`, `contradicts`, `not_bahasa`.
- `field_key` must exist in the definition, and `value` must match the field's shape. An item that fails either is dropped and counted.

## Summary (`call_site = hub.summary`)

**Input**:
- **confirmed** (`current`) Profil and Guideline values only
- open Requests
- the Client's name and Noktah Brand

No Intake raw material, and no pending values.

**Response schema**:

```json
{
  "siapa": "1-2 sentences: who the Client is",
  "promo_berjalan": ["current promo lines with valid-until"],
  "aturan_kunci": ["the 3-6 content rules that most often matter"],
  "permintaan_terbuka": ["open requests, newest first"]
}
```

The response is rendered as the Ringkasan with "dibuat otomatis · {date}". It is **never** used as a source and is not editable (G-27).
