"""Address variation ("rejig") generator.

Produces alternate, still-deliverable spellings of a real address: street-suffix
abbreviations, directionals, punctuation, and optional unit lines. Every variant
keeps the same street number, street name, city, state and ZIP, so USPS resolves
them to the same mailbox.
"""
import itertools
import random
import re

SUFFIX = {
    "street": ["Street", "St", "St."], "avenue": ["Avenue", "Ave", "Ave."], "road": ["Road", "Rd", "Rd."],
    "drive": ["Drive", "Dr", "Dr."], "lane": ["Lane", "Ln", "Ln."], "court": ["Court", "Ct", "Ct."],
    "boulevard": ["Boulevard", "Blvd", "Blvd."], "circle": ["Circle", "Cir", "Cir."],
    "place": ["Place", "Pl", "Pl."], "terrace": ["Terrace", "Ter", "Ter."], "way": ["Way"],
    "parkway": ["Parkway", "Pkwy", "Pkwy."], "trail": ["Trail", "Trl", "Trl."], "highway": ["Highway", "Hwy", "Hwy."],
}
ALIAS = {"st": "street", "st.": "street", "ave": "avenue", "av": "avenue", "ave.": "avenue", "rd": "road", "rd.": "road",
         "dr": "drive", "dr.": "drive", "ln": "lane", "ln.": "lane", "ct": "court", "ct.": "court", "blvd": "boulevard",
         "blvd.": "boulevard", "cir": "circle", "pl": "place", "ter": "terrace", "pkwy": "parkway", "trl": "trail",
         "hwy": "highway"}
DIRS = {"north": ["North", "N", "N."], "south": ["South", "S", "S."], "east": ["East", "E", "E."], "west": ["West", "W", "W."]}
DIR_ALIAS = {"n": "north", "n.": "north", "s": "south", "s.": "south", "e": "east", "e.": "east", "w": "west", "w.": "west"}
UNIT_STYLES = ["Apt {}", "Unit {}", "#{}", "Ste {}", "Apt. {}", "Unit #{}"]


def _variants_of_word(w):
    k = w.lower()
    if k in SUFFIX or k in ALIAS:
        return SUFFIX[ALIAS.get(k, k)]
    if k in DIRS or k in DIR_ALIAS:
        return DIRS[DIR_ALIAS.get(k, k)]
    return [w]


def street_variants(line1):
    words = line1.split()
    if not words:
        return []
    opts = [_variants_of_word(w) for w in words]
    combos = {" ".join(c) for c in itertools.islice(itertools.product(*opts), 200)}
    out = set(combos)
    for c in combos:
        out.add(c.replace(".", ""))
        out.add(c.title() if c.islower() else c)
    return sorted(out)


def generate(addr, count=10, seed=None, use_units=True, unit_values=None, keep_original=True):
    """addr: dict with address1, address2, city, state, zip. Returns list of unique dicts."""
    rng = random.Random(seed)
    base2 = (addr.get("address2") or "").strip()
    lines1 = street_variants(addr.get("address1", ""))
    unit_values = unit_values or [str(i) for i in range(1, 10)] + list("ABCD")
    cands = []
    for l1 in lines1:
        cands.append((l1, base2))
        m = re.match(r"^\s*(?:[A-Za-z]{2,6}\.?|#)\s*#?\s*(\S.*)$", base2) if base2 else None
        if use_units and m:
            for st in ["Apt {}", "Unit {}", "#{}", "Ste {}", "Apt. {}"]:
                cands.append((l1, st.format(m.group(1))))
        if use_units and not base2:
            for st in UNIT_STYLES:
                for v in unit_values:
                    cands.append((l1, st.format(v)))
    rng.shuffle(cands)
    seen, out = set(), []
    orig = (addr.get("address1", "").strip(), base2)
    seen.add(tuple(re.sub(r"[\W_]+", "", x).lower() for x in orig))
    if keep_original:
        out.append(dict(addr))
    for l1, l2 in cands:
        key = (re.sub(r"[\W_]+", "", l1).lower(), re.sub(r"[\W_]+", "", l2).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({**addr, "address1": l1, "address2": l2})
        if len(out) >= count:
            break
    return out[:count]
