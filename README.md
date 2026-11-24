# csv-normalize

Normalize a messy CSV to an exact target schema, and emit a schema-valid JSON manifest.

Python 3.9+, standard library only (no dependencies).

## Run

```bash
python3 csv_sample.py ./out
```

It writes four artifacts into `./out`:

| File | What it is |
|---|---|
| `input_messy.csv` | the deliberately messy source |
| `output_normalized.csv` | the exact target schema, fixed header and row order |
| `manifest.json` | schema-valid JSON manifest |
| `validation.txt` | the validator's output — every acceptance criterion checked |

## What the sample handles

The messy source deliberately includes the failure modes that break a naive
`csv.DictReader` pipeline:

- **Bare thousands separators** (`1,200`, `3,000`) written unquoted. A naive CSV
  split turns these into two fields and shifts every later column, silently
  corrupting the row. The parser protects a bare thousands separator *before*
  tokenizing rather than guessing afterwards which adjacent fields belonged
  together — guessing is ambiguous when a row contains more than one split
  number.
- **Quoted fields with leading spaces** (` "1.695,00" `). Python's `csv` only
  treats `"` as a quote character when it starts a field, so `skipinitialspace=True`
  is required or the field splits on the comma.
- **Mixed date formats**: ISO `2024-01-05`, `05/02/2024`, `2024/03/20`,
  `Mar 12, 2024`, `22 Apr 2024` → all emitted as `YYYY-MM-DD`.
- **Mixed-case currency** (`usd`, `jpy`, `nzd`) → uppercase ISO 4217.
- **A price with a comma decimal separator** (`1.695,00`) → `1695.00`.
- **Non-required columns** (`Notes`, `Ref`) → dropped.
- **Whitespace** around cells and inside the header → trimmed, header canonicalized
  (`Shipment ID ` → `shipment_id`).

## Rules

- Target header is fixed and ordered:
  `shipment_id,origin,destination,units,unit_price,currency,ship_date`
- `units` → integer, thousands separators removed.
- `unit_price` → decimal, half-up to exactly two places; currency symbols and
  separators removed.
- `currency` → uppercased and validated against ISO 4217 alphabetic codes. A value
  that is not a valid code becomes `null` **only** where the schema allows null.
- `ship_date` → parsed against an ordered list of formats, emitted as `YYYY-MM-DD`.
- Non-required columns are dropped entirely.
- No row is silently dropped. If a row cannot be realigned to the expected column
  count, the run fails loudly with the line number and the raw fields rather
  than passing off a partial result.

## Evidence

`validation.txt` is the real output of the validator, not a transcription:

```
13 passed, 0 failed
```

## Why a validator and not just a transform

A transform that runs without error is not the same as output that meets the
schema. The validator checks each acceptance criterion independently — header
byte-exactness, row-count preservation, uniqueness, integer units, two-decimal
prices, ISO 4217 uppercase-or-null, ISO dates, no stray whitespace, nulls only
for genuinely unknown values, no empty required fields — and its output ships
with the files so the result can be checked without trusting the transform.
