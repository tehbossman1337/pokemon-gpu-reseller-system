"""Turn retailer emails into order events.

Each retailer is detected by sender domain; status by subject/body keywords.
Anything that can't be parsed confidently is returned as None so the sync
puts it in the Review queue instead of guessing.
"""
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

RETAILERS = {
    "Pokemon Center": ["pokemoncenter.com", "pokemon.com"],
    "Target": ["target.com"],
    "Walmart": ["walmart.com"],
    "Best Buy": ["bestbuy.com"],
    "GameStop": ["gamestop.com"],
    "Costco": ["costco.com"],
    "Sam's Club": ["samsclub.com"],
}

STATUS_RULES = [  # first match wins; checked against subject, then body head
    ("cancelled", r"cancel+ed|has been cancel|order cancellation|unable to (fulfill|ship)|we.?re sorry.*order"),
    ("delivered", r"\bdelivered\b|has been delivered|was delivered|ready for pickup|picked up"),
    ("shipped", r"\bshipped\b|on its way|has shipped|out for delivery|tracking number|shipment (confirmation|notification)"),
    ("confirmed", r"order (confirmation|confirmed|received|placed|summary|details)|thank(s| you) for (your|shopping|ordering).{0,60}|thank(s| you) for (your )?.{0,40}order|we.?ve received your order|your .{0,40}order|order #|confirmation"),
]

ORDER_NO = [
    r"order\s*(?:number|no\.?|#|id)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{5,24})",
    r"order\s*#\s*([A-Z0-9][A-Z0-9\-]{5,24})",
    r"(?:confirmation|reference)\s*(?:number|no\.?|#|code|id)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{5,24})",
    r"\border\s*[:\-]\s*#?\s*([A-Z0-9][A-Z0-9\-]{5,24})",
    r"\border\s+([0-9]{7,15})\b",
    r"#\s*([0-9]{6,15})",
]
TRACKING = [
    r"\b(1Z[0-9A-Z]{16})\b",
    r"\b(9[2345]\d{18,22})\b",
    r"tracking\s*(?:number|no\.?|#)?\s*[:#]?\s*([A-Z0-9]{10,30})",
]
TOTAL = r"(?:order\s+total|total(?:\s+charged)?|grand total|amount charged)\s*[:\-]?\s*\$\s*([\d,]+\.\d{2})"
ITEM_LINE = [
    # "2 x Pokemon TCG Elite Trainer Box ... $99.98"  /  "Qty: 2"
    r"^\s*(\d{1,3})\s*[x×]\s+(.{6,120}?)(?:\s+[-–@]\s*|\s+)\$\s*([\d,]+\.\d{2})\s*$",
    r"^\s*(.{6,120}?)\s+(?:qty|quantity)\s*[:x]?\s*(\d{1,3})\s+(?:\$\s*)?([\d,]+\.\d{2})\s*$",
]
SKU_RE = re.compile(r"(?:sku|dpci|item\s*#|item number|model)\s*[:#]?\s*([A-Z0-9\-]{5,20})", re.I)


BRAND_WORDS = {"Pokemon Center": ["pokemon center", "pokémon center", "pokemoncenter"], "Target": ["target.com", "target order"],
               "Walmart": ["walmart"], "Best Buy": ["best buy", "bestbuy"], "GameStop": ["gamestop"],
               "Costco": ["costco"], "Sam's Club": ["sam's club", "samsclub"]}


def detect_retailer(sender, extra="", default=None):
    """Match by sender domain; if that fails (forwarded / Hide-My-Email mail), fall back to
    the Reply-To header or brand names in the subject/body head passed via `extra`."""
    s = (sender or "").lower()
    for name, doms in RETAILERS.items():
        if any(d in s for d in doms):
            return name
    e = (extra or "").lower()
    for name, doms in RETAILERS.items():
        if any(d in e for d in doms):
            return name
    return default


def detect_status(subject, body):
    for hay in (subject or "", (body or "")[:1500]):
        for status, pat in STATUS_RULES:
            if re.search(pat, hay, re.I):
                return status
    return None


def _first(patterns, text, flags=re.I):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip()
    return ""


NOT_ITEM = re.compile(r"\b(total|subtotal|sub-total|tax|shipping|handling|discount|savings?|save|from|gift|balance|estimated|points|"
                      r"was|price|payment|visa|mastercard|amex|paypal|promo|coupon|order|delivery|fee|refund)\b", re.I)
PRICE = re.compile(r"\$\s*([\d,]+\.\d{2})")
QTY_ONLY = re.compile(r"^(?:qty|quantity)\s*[:x]?\s*(\d{1,3})\b|^x\s*(\d{1,3})$|^(\d{1,3})\s*x$", re.I)


def _name_like(l):
    return len(re.findall(r"[A-Za-z]", l)) >= 6 and not PRICE.fullmatch(l.strip()) and not re.match(r"^(item|price|qty|quantity|sku|total|subtotal)\b[:\s]*$", l.strip(), re.I)


def _loose_items(lines):
    """Layouts where name, quantity and price sit on separate lines (typical HTML email tables)."""
    items = []
    for i, ln in enumerate(lines):
        m = QTY_ONLY.match(ln.strip())
        if not m:
            continue
        qty = int(next(g for g in m.groups() if g))
        name = next((lines[j].strip() for j in range(i - 1, max(-1, i - 5), -1) if _name_like(lines[j]) and not NOT_ITEM.search(lines[j].split("$")[0][:40] or "") ), None)
        if not name:
            name = next((lines[j].strip() for j in range(i - 1, max(-1, i - 5), -1) if _name_like(lines[j])), None)
        window = lines[max(0, i - 1):i + 4]
        pm = next((PRICE.search(w) for w in window if PRICE.search(w)), None)
        if name and pm and qty:
            price = float(pm.group(1).replace(",", ""))
            items.append({"name": PRICE.sub("", name).strip(" -–:|"), "qty": qty, "price": round(price / qty, 2)})
    if items:
        return items
    for ln in lines:  # last resort: "Product name   $19.99" with one unit
        m = re.match(r"^(.{8,120}?)\s{1,}[-–:|]?\s*\$\s*([\d,]+\.\d{2})\s*$", ln.strip())
        if m and _name_like(m.group(1)) and not NOT_ITEM.search(m.group(1)):
            items.append({"name": m.group(1).strip(" -–:|"), "qty": 1, "price": float(m.group(2).replace(",", ""))})
    return items


def parse_items(body):
    items = []
    lines = body.splitlines()
    for ln in lines:
        m = re.match(ITEM_LINE[0], ln, re.I)
        if m:
            qty, name, price = int(m.group(1)), m.group(2), float(m.group(3).replace(",", ""))
            items.append({"name": name.strip(" -–"), "qty": qty, "price": round(price / qty, 2) if qty else price})
            continue
        m = re.match(ITEM_LINE[1], ln, re.I)
        if m:
            name, qty, price = m.group(1), int(m.group(2)), float(m.group(3).replace(",", ""))
            items.append({"name": name.strip(" -–"), "qty": qty, "price": price})
    if not items:  # column layout:  name   2   $49.99   $99.98
        for ln in lines:
            m = re.match(r"^(.{6,120}?)\s{2,}(\d{1,3})\s{2,}\$\s*([\d,]+\.\d{2})(?:\s{2,}\$\s*[\d,]+\.\d{2})?\s*$", ln)
            if m and _name_like(m.group(1)):
                items.append({"name": m.group(1).strip(" -–"), "qty": int(m.group(2)), "price": float(m.group(3).replace(",", ""))})
    if not items:
        items = _loose_items([l for l in lines if l.strip()])
    # attach SKUs found on the line right after an item
    for i, ln in enumerate(lines):
        sm = SKU_RE.search(ln)
        if sm and items:
            for it in items:
                if it["name"] and it["name"] in "\n".join(lines[max(0, i - 3):i + 1]):
                    it.setdefault("sku", sm.group(1))
    return items


def parse_email(sender, subject, body, date_header=None, extra="", default=None):
    """Return (event_dict | None, reason_if_none)."""
    retailer = detect_retailer(sender, extra or f"{subject}\n{(body or '')[:800]}", default)
    if not retailer:
        return None, "unknown sender"
    status = detect_status(subject, body)
    if not status:
        return None, "could not tell order status"
    text = f"{subject}\n{body}"
    order_no = _first(ORDER_NO, text)
    if not order_no:
        return None, "no order number found"
    total = _first([TOTAL], body)
    tracking = _first(TRACKING, body) if status in ("shipped", "delivered") else ""
    try:
        d = parsedate_to_datetime(date_header).date().isoformat() if date_header else None
    except Exception:
        d = None
    return {
        "retailer": retailer,
        "order_number": order_no.upper(),
        "status": status,
        "date": d or datetime.now().date().isoformat(),
        "total": float(total.replace(",", "")) if total else None,
        "tracking": tracking,
        "items": parse_items(body) if status in ("confirmed", "shipped", "delivered") else [],
    }, ""
