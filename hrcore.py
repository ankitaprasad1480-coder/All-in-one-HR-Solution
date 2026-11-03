"""CSV cleaning and shared constants for the HR dashboard (standard library only)."""
import csv
import io
import re
from datetime import date

CURRENCIES = ["AED", "USD", "INR", "EUR", "GBP", "SAR", "QAR", "KWD", "BHD", "OMR", "PKR", "LKR", "BDT", "THB",
              "VND", "PHP", "IDR", "MYR", "SGD", "CAD", "AUD", "ZAR", "EGP", "TRY"]
INDUSTRIES = ["Technology", "Hospitality", "Retail", "Healthcare", "Education", "Manufacturing", "Finance",
              "Construction", "Logistics", "Other"]
SIZES = ["1-10", "11-50", "51-200", "201-1000", "1000+"]

ALIASES = {
    "name": ["name", "employee name", "emp name", "full name", "employee"],
    "designation": ["designation", "role", "job title", "title", "position"],
    "department": ["department", "dept", "team"],
    "location": ["location", "country", "office", "work location"],
    "doj": ["doj", "date of joining", "joining date", "join date", "hire date", "start date", "date joined"],
    "dor": ["dor", "date of resignation", "date of leaving", "leaving date", "exit date", "last working day",
            "lwd", "termination date", "date of exit"],
    "dob": ["dob", "date of birth", "birth date"],
    "ctc": ["ctc", "annual ctc", "annual salary", "yearly salary", "annual package"],
    "monthly": ["monthly salary", "monthly gross", "fixed gross salary", "gross salary", "monthly ctc"],
    "currency": ["currency", "salary currency"],
    "salary_text": ["starting salary"],
}
LOCATION_MAP = {"dubai": "UAE", "abu dhabi": "UAE", "sharjah": "UAE", "uae": "UAE", "u a e": "UAE",
                "united arab emirates": "UAE", "uk": "UK", "united kingdom": "UK", "usa": "USA", "us": "USA",
                "united states": "USA"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
MONTHS["spe"] = 9  # "Spet" typo seen in real files

TEMPLATE_CSV = (
    "Name,Designation,Department,Location,Date of Joining,Date of Leaving,Date of Birth,Annual CTC,Currency\n"
    "Asha Verma,Software Engineer,Engineering,India,12-Jan-2023,,04-Mar-1995,960000,INR\n"
    "Omar Khalid,Sales Executive,Sales,UAE,01-Jun-2022,31-Aug-2025,17-Nov-1990,96000,AED\n"
    "Lena Fischer,Designer,Design,Germany,2024-02-15,,1998-07-22,42000,EUR\n"
)

_ALIAS_LOOKUP = {}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


for _k, _v in ALIASES.items():
    for _a in _v:
        _ALIAS_LOOKUP[norm(_a)] = _k


def clean_text(s, maxlen=80):
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:maxlen]


def _year(t):
    y = int(t)
    if len(t) == 2:
        y += 2000 if y <= (date.today().year % 100) + 1 else 1900
    return y


def parse_date(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or s.lower() in ("nan", "none", "na", "n/a", "-", "nat"):
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"^(\d{1,2})[-/. ]([A-Za-z]{3})[A-Za-z]*\.?[-/. ,]*(\d{2,4})", s)
        if m:
            d, mo, y = int(m[1]), MONTHS.get(m[2].lower()), _year(m[3])
        else:
            m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)  # day first
            if not m:
                return None
            d, mo, y = int(m[1]), int(m[2]), _year(m[3])
    try:
        return date(y, mo, d)
    except (ValueError, TypeError):
        return None


def parse_amount(s):
    t = re.sub(r"[^0-9.]", "", str(s or "").replace(",", ""))
    try:
        return float(t) if t else 0.0
    except ValueError:
        return 0.0


def detect_currency(values, default):
    for v in values:
        m = re.match(r"^\s*([A-Za-z]{3})(?![A-Za-z])", str(v or ""))
        if m and m[1].upper() in CURRENCIES:
            return m[1].upper()
    return default


def _delimiter(text):
    lines = text.splitlines()[:8]
    return max([",", ";", "\t"], key=lambda d: sum(l.count(d) for l in lines))


def clean_records(text, default_currency):
    """Return (records, report) or raise ValueError with a message meant for the user."""
    text = text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text), delimiter=_delimiter(text)))
    hi = None
    for i, row in enumerate(rows[:12]):
        keys = {_ALIAS_LOOKUP.get(norm(c)) for c in row} - {None}
        if "name" in keys and len(keys) >= 3:
            hi = i
            break
    need = ("Your file needs column headings including Name, Date of joining, and a salary column "
            "(Annual CTC or Monthly salary). Download the template to see an example.")
    if hi is None:
        raise ValueError("Could not find the column headings. " + need)
    cols = {}
    for idx, c in enumerate(rows[hi]):
        k = _ALIAS_LOOKUP.get(norm(c))
        if k and k not in cols:
            cols[k] = idx
    if "doj" not in cols or not ("ctc" in cols or "monthly" in cols):
        raise ValueError("Missing columns. " + need)

    def cell(r, k):
        i = cols.get(k)
        return r[i] if i is not None and i < len(r) else ""

    today = date.today()
    rep = {"rows": 0, "people": 0, "merged": 0, "bad_date_order": 0, "missing_join_date": 0,
           "invalid_birth": 0, "skipped_blank": 0}
    people = {}
    for r in rows[hi + 1:]:
        name = clean_text(cell(r, "name"))
        if not name:
            rep["skipped_blank"] += 1
            continue
        rep["rows"] += 1
        j, x = parse_date(cell(r, "doj")), parse_date(cell(r, "dor"))
        if j and x and x < j:
            rep["bad_date_order"] += 1
            j = None
        b = parse_date(cell(r, "dob"))
        if str(cell(r, "dob")).strip() and (not b or not (1940 <= b.year <= today.year - 14)):
            rep["invalid_birth"] += 1
            b = None
        ctc = parse_amount(cell(r, "ctc")) or parse_amount(cell(r, "monthly")) * 12
        loc = clean_text(cell(r, "location"), 60) or "Unknown"
        loc = LOCATION_MAP.get(norm(loc), loc.title() if len(loc) > 3 else loc.upper())
        rec = {"n": name, "des": clean_text(cell(r, "designation")) or "-",
               "d": clean_text(cell(r, "department"), 60) or "Unassigned", "l": loc, "j": j, "x": x, "b": b,
               "c": detect_currency([cell(r, "currency"), cell(r, "salary_text")], default_currency),
               "ctc": int(ctc)}
        people.setdefault(name.lower(), []).append(rec)
    out = []
    for group in people.values():
        group.sort(key=lambda g: (g["j"] is not None, g["j"] or date.min))
        last = dict(group[-1])
        last["j"] = group[0]["j"]
        last["b"] = next((g["b"] for g in group if g["b"]), None)
        out.append(last)
    if not out:
        raise ValueError("No employee rows were found under the headings.")
    if len(out) > 5000:
        raise ValueError("That file has more than 5,000 employees, which is above the limit.")
    rep["people"] = len(out)
    rep["merged"] = rep["rows"] - len(out)
    rep["missing_join_date"] = sum(1 for o in out if not o["j"])
    iso = lambda d: d.isoformat() if d else None
    recs = [{"n": o["n"], "des": o["des"], "d": o["d"], "l": o["l"], "j": iso(o["j"]), "x": iso(o["x"]),
             "b": iso(o["b"]), "c": o["c"], "ctc": o["ctc"]} for o in out]
    return recs, rep
