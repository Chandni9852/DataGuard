"""Column profiling for uploaded data.

Detection is informational. It tells the user what the data looks like; it never decides
pass or fail. Validation rules own that decision. A column is typed by its dominant pattern
(>= TYPE_THRESHOLD of non-blank values), and the match ratio is reported so a column that is
"decimal" at 98.7% visibly contains bad values instead of hiding them.
"""
import pandas as pd

TYPE_THRESHOLD = 0.90
SAMPLE_SIZE = 3

_INT = r"^-?\d+$"
_DEC = r"^-?\d+(\.\d+)?$"
_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _profile_column(name: str, s: pd.Series) -> dict:
    stripped = s.str.strip()
    blank = stripped == ""
    non_blank = stripped[~blank]
    n = len(non_blank)

    inferred, confidence = "string", 1.0
    if n:
        int_ratio = non_blank.str.match(_INT).sum() / n
        dec_ratio = non_blank.str.match(_DEC).sum() / n
        date_mask = non_blank.str.match(_DATE)
        valid_dates = pd.to_datetime(non_blank[date_mask], format="%Y-%m-%d", errors="coerce").notna().sum()
        date_ratio = valid_dates / n
        for label, ratio in (("integer", int_ratio), ("decimal", dec_ratio), ("date", date_ratio)):
            if ratio >= TYPE_THRESHOLD:
                inferred, confidence = label, float(ratio)
                break

    return {
        "name": name,
        "inferred_type": inferred,
        "type_confidence": round(confidence, 4),
        "null_count": int(blank.sum()),
        "distinct_count": int(non_blank.nunique()),
        "sample_values": [str(v) for v in non_blank.drop_duplicates().head(SAMPLE_SIZE)],
    }


def detect_schema(df: pd.DataFrame) -> list[dict]:
    return [_profile_column(str(c), df[c]) for c in df.columns]
