"""Error classification and correction rules for portal field errors."""

DUPLICATE = "duplicate"
AMOUNT_RANGE = "amount_range"
REMARK_TOO_LONG = "remark_too_long"
UNKNOWN = "unknown"


def classify_error(message):
    if "单号重复" in message:
        return DUPLICATE
    if "金额超出范围" in message:
        return AMOUNT_RANGE
    if "备注过长" in message:
        return REMARK_TOO_LONG
    return UNKNOWN


def fix_amount(amount, min_value, max_value):
    """Clamp the amount into the portal-published [min, max] range."""
    amount = float(amount)
    if max_value is not None and amount > max_value:
        return max_value
    if min_value is not None and amount < min_value:
        return min_value
    return amount


def fix_remark(remark, max_length):
    """Truncate the remark to the portal-published limit."""
    return remark[:max_length]

