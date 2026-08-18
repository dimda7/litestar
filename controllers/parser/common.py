import re


PREFIX = "parser"


# A model lcn like 'M9.6.5': the digits after the letter prefix and before the
# first dot are id_train_type; the rest of the path is carried over as is.
_MODEL_LCN_RE = re.compile(r"^\D*(\d+)(?:\.(.*))?$")


def parse_model_lcn(lcn: str) -> tuple[int, str] | None:
    """Extract (id_train_type, rest_of_path) from an lcn like 'M9.6.5' -> (9, '6.5'); 'M9' -> (9, '')."""
    match = _MODEL_LCN_RE.match(lcn)
    if not match:
        return None
    return int(match.group(1)), match.group(2) or ""
