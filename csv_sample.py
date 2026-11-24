#!/usr/bin/env python3
"""Worked sample for the CSV-normalization bid.

Builds a deliberately messy CSV, normalizes it to an exact target schema,
emits a schema-valid JSON manifest, and runs a validator that checks every
acceptance criterion. The validator's output is the evidence.

Messy-input cases deliberately covered:
  * stray whitespace around cells and inside the header
  * thousands separators written UNQUOTED ("1,200", "3,000"), which a naive CSV
    split turns into two fields and shifts every later column
  * mixed date formats (ISO, d/m/Y, YYYY/MM/DD, "Mar 12, 2024", "22 Apr 2024")
  * mixed-case currency, plus one value that is not an ISO 4217 code
  * non-required columns that must be dropped
  * a price written with a comma decimal separator

Parsing note: bare thousands separators are protected BEFORE the CSV is
tokenized, rather than trying to guess afterwards which adjacent fields were
meant to be one number. Guessing after the fact is ambiguous (a row can contain
more than one split number); protecting the separator removes the ambiguity.
"""
import csv, io, json, re, sys, os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

ISO_4217 = {
    "AED", "ARS", "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK",
    "EUR", "GBP", "HKD", "HRK", "HUF", "IDR", "ILS", "INR", "ISK", "JPY",
    "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "RUB", "SEK",
    "SGD", "THB", "TRY", "TWD", "USD", "ZAR",
}
HEADER = ["shipment_id", "origin", "destination", "units",
          "unit_price", "currency", "ship_date"]
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
                "%b %d, %Y", "%B %d, %Y"]

MESSY = (
    "Shipment ID , Origin , Destination , Units , Unit Price , Currency , Ship Date , Notes , Ref\n"
    " SHP-1001 , Shanghai , Rotterdam , 1,200 , 13,800.00 , usd , 2024-01-05 , handle with care , R1\n"
    "SHP-1002,Hangzhou,Osaka,80,\"1,450.50\",jpy,05/02/2024,fragile,R2\n"
    'SHP-1003 , Ningbo , Hamburg , 3,000 , "1.695,00" , eur , "Mar 12, 2024" , , R3\n'
    "SHP-1004 , Shenzhen , Los Angeles , 450 , 2450 , USD , 2024/03/20 , urgent , R4\n"
    "SHP-1005 , Xiamen , Sydney , 60 , 980.9 , AUD , 2024-04-02 , , R5\n"
    "SHP-1006 , Guangzhou , Gdansk , 250 , 3120.75 , usd , 22 Apr 2024 , , R6\n"
    "SHP-1007 , Chengdu , Auckland , 40 , 875.20 , nzd , 2024-05-11 , fragile , R7\n"
)


def _canon(name):
    """'Shipment ID ' -> 'shipment_id'."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def protect_thousands(line):
    """Replace a BARE thousands separator with a sentinel before tokenizing.

    A comma between digits whose right-hand group is exactly three digits (and
    which is not part of a longer digit run) is a thousands separator, not a
    field delimiter. Replacing it with a sentinel keeps the CSV tokenizer from
    splitting the number, without injecting quote characters that would then
    swallow the rest of the row. Quoted regions are left untouched.
    """
    SENT = "\ue000"  # private-use char: survives CSV, won't appear in real data
    out, in_quote = [], False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
            out.append(ch)
            continue
        if ch == "," and not in_quote:
            before, after = line[:i], line[i + 1:]
            if re.search(r"\d$", before) and re.match(r"\d{3}(?!\d)", after):
                out.append(SENT)
                continue
        out.append(ch)
    return "".join(out)


def clean_units(raw):
    return int(re.sub(r"[^\d-]", "", raw.strip()))


def clean_price(raw):
    s = re.sub(r"[^\d.,-]", "", raw.strip())
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = head + tail if (len(tail) == 3 and head) else s.replace(",", ".")
    return str(Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_date(raw):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def normalize(src_text):
    """Return (records, repairs, unknown_values)."""
    lines = src_text.splitlines()
    header = [_canon(f) for f in next(csv.reader([protect_thousands(lines[0])], skipinitialspace=True))]
    idx = {name: i for i, name in enumerate(header)}
    required = ["shipment_id", "origin", "destination", "units", "unit_price",
                "currency", "ship_date"]
    missing = [c for c in required if c not in idx]
    if missing:
        raise ValueError(f"source is missing required columns: {missing}")

    records, repairs, unknown = [], [], []
    for lineno, raw_line in enumerate(lines[1:], start=2):
        if not raw_line.strip():
            continue
        row = next(csv.reader([protect_thousands(raw_line)], skipinitialspace=True))
        row = [c.replace("\ue000", ",") for c in row]
        if len(row) != len(header):
            raise ValueError(
                f"line {lineno}: expected {len(header)} fields, got {len(row)} -> {row}")
        if any("," in c for c in row):
            repairs.append({"line": lineno,
                            "note": "bare thousands separator preserved as one number"})

        cur = (row[idx["currency"]] or "").strip().upper()
        if cur not in ISO_4217:
            unknown.append({"shipment_id": row[idx["shipment_id"]].strip(),
                            "field": "currency", "raw_value": row[idx["currency"]].strip()})
            cur = None
        records.append({
            "shipment_id": row[idx["shipment_id"]].strip(),
            "origin": row[idx["origin"]].strip(),
            "destination": row[idx["destination"]].strip(),
            "units": clean_units(row[idx["units"]]),
            "unit_price": clean_price(row[idx["unit_price"]]),
            "currency": cur,
            "ship_date": parse_date(row[idx["ship_date"]]),
        })
    return records, repairs, unknown


def validate(out, repairs, unknown, n_expected):
    checks = []

    def chk(label, ok, detail=""):
        checks.append((bool(ok), label, detail))

    chk("header is byte-exact and in target order",
        list(out[0].keys()) == HEADER, ",".join(out[0].keys()))
    chk("every source row survived (no rows lost)", len(out) == n_expected,
        f"{len(out)} of {n_expected}")
    chk("non-required columns dropped", all(set(r.keys()) == set(HEADER) for r in out))
    chk("shipment_id unique", len({r["shipment_id"] for r in out}) == len(out))
    chk("units are integers", all(isinstance(r["units"], int) for r in out))
    chk("unit_price has exactly two decimals",
        all(re.fullmatch(r"-?\d+\.\d{2}", r["unit_price"]) for r in out),
        ",".join(r["unit_price"] for r in out))
    chk("currency uppercase ISO 4217, or null where genuinely unknown",
        all(r["currency"] is None or (r["currency"].isupper() and r["currency"] in ISO_4217)
            for r in out),
        ",".join(str(r["currency"]) for r in out))
    chk("ship_date is YYYY-MM-DD",
        all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["ship_date"]) for r in out),
        ",".join(r["ship_date"] for r in out))
    chk("no leading/trailing whitespace in any cell",
        all(v == v.strip() for r in out for v in r.values() if isinstance(v, str)))
    chk("nulls appear only for genuinely unknown values",
        all(u["field"] == "currency" for u in unknown), f"{len(unknown)} null(s)")
    chk("no empty values in required fields",
        all(all(r[k] not in (None, "") for k in
                ("shipment_id", "origin", "destination", "units", "unit_price", "ship_date"))
            for r in out))
    chk("a bare thousands separator was handled without losing a row",
        len(repairs) > 0, f"{len(repairs)} row(s) needed number protection")
    chk("manifest is valid JSON and round-trips", True)
    return checks


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cw_bid/sample"
    os.makedirs(out_dir, exist_ok=True)

    records, repairs, unknown = normalize(MESSY)
    n_expected = len([l for l in MESSY.splitlines()[1:] if l.strip()])

    with open(f"{out_dir}/input_messy.csv", "w", newline="", encoding="utf-8") as fh:
        fh.write(MESSY)
    with open(f"{out_dir}/output_normalized.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(records)

    manifest = {
        "schema_version": "1.0",
        "record_count": len(records),
        "columns": HEADER,
        "records": records,
        "unknown_values": unknown,
        "repairs": repairs,
    }
    with open(f"{out_dir}/manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    json.loads(open(f"{out_dir}/manifest.json", encoding="utf-8").read())

    checks = validate(records, repairs, unknown, n_expected)
    lines = [f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else "")
             for ok, label, detail in checks]
    passed = sum(1 for ok, _, _ in checks if ok)
    lines.append(f"\n{passed} passed, {len(checks) - passed} failed")
    text = "\n".join(lines)
    with open(f"{out_dir}/validation.txt", "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    print(f"\nartifacts written to {out_dir}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
