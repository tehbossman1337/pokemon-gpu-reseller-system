"""Tiny QR code generator (byte mode, error level L, versions 1-6) so pairing needs no extra packages."""

# version -> (data codewords per block, ec codewords per block, blocks, alignment positions)
_V = {1: (19, 7, 1, []), 2: (34, 10, 1, [6, 18]), 3: (55, 15, 1, [6, 22]), 4: (80, 20, 1, [6, 26]),
      5: (108, 26, 1, [6, 30]), 6: (68, 18, 2, [6, 34])}
_CAP = {1: 17, 2: 32, 3: 53, 4: 78, 5: 106, 6: 134}

_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _mul(g, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(n):
            rem[i] ^= _mul(gen[i + 1], f)
    return rem


def _bch_format(bits5):
    v = bits5 << 10
    for i in range(14, 9, -1):
        if v >> i & 1:
            v ^= 0x537 << (i - 10)
    return ((bits5 << 10) | v) ^ 0x5412


def make(text, mask=None):
    """Return a square matrix (list of lists of bool, True = dark)."""
    raw = text.encode("utf-8")
    ver = next((v for v in range(1, 7) if len(raw) <= _CAP[v]), None)
    if ver is None:
        raise ValueError("text too long for QR")
    dpb, ecn, nblk, align = _V[ver]
    total = dpb * nblk
    bits = "0100" + format(len(raw), "08b") + "".join(format(b, "08b") for b in raw)
    bits += "0" * min(4, total * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(cw) < total:
        cw.append(pad[(len(cw) - len(bits) // 8) % 2])
    blocks = [cw[i * dpb:(i + 1) * dpb] for i in range(nblk)]
    ecs = [_rs(b, ecn) for b in blocks]
    final = [blocks[b][i] for i in range(dpb) for b in range(nblk)] + [ecs[b][i] for i in range(ecn) for b in range(nblk)]
    bitstream = [int(c) for byte in final for c in format(byte, "08b")]

    n = 17 + 4 * ver
    M = [[None] * n for _ in range(n)]

    def put(r, c, v):
        if 0 <= r < n and 0 <= c < n:
            M[r][c] = v

    def finder(r0, c0):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                dark = 0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                put(r0 + dr, c0 + dc, dark)
    finder(0, 0); finder(0, n - 7); finder(n - 7, 0)
    for i in range(8, n - 8):
        put(6, i, i % 2 == 0); put(i, 6, i % 2 == 0)
    for ar in align:
        for ac in align:
            if (ar, ac) in ((6, 6),) or (ar == 6 and ac == align[-1]) or (ac == 6 and ar == align[-1]):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(ar + dr, ac + dc, max(abs(dr), abs(dc)) != 1)
    put(4 * ver + 9, 8, True)  # dark module
    reserved = [[M[r][c] is not None for c in range(n)] for r in range(n)]
    for i in range(9):
        for (r, c) in ((8, i), (i, 8)):
            reserved[r][c] = True
    for i in range(8):
        reserved[8][n - 1 - i] = True
        reserved[n - 1 - i][8] = True
    # data placement
    pos, up, c = 0, True, n - 1
    cells = []
    while c > 0:
        if c == 6:
            c -= 1
        rng = range(n - 1, -1, -1) if up else range(n)
        for r in rng:
            for cc in (c, c - 1):
                if not reserved[r][cc] and M[r][cc] is None:
                    cells.append((r, cc))
        up = not up
        c -= 2
    masks = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
             lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
             lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]

    def build(mk):
        G = [row[:] for row in M]
        for i, (r, cc) in enumerate(cells):
            b = bitstream[i] if i < len(bitstream) else 0
            G[r][cc] = bool(b) ^ masks[mk](r, cc)
        f = _bch_format((0b01 << 3) | mk)  # error level L = 01
        for i in range(15):
            bit = bool(f >> i & 1)
            # around top-left finder
            if i < 6: G[i][8] = bit
            elif i < 8: G[i + 1][8] = bit
            else: G[8][14 - i if i >= 9 else 7] = bit
            if i < 8: G[8][n - 1 - i] = bit
            else: G[n - 15 + i][8] = bit
        return G

    def penalty(G):
        p = 0
        for g in (G, list(zip(*G))):
            for row in g:
                run = 1
                for i in range(1, n):
                    if row[i] == row[i - 1]:
                        run += 1
                    else:
                        p += run - 2 if run >= 5 else 0
                        run = 1
                p += run - 2 if run >= 5 else 0
        for r in range(n - 1):
            for c in range(n - 1):
                if G[r][c] == G[r][c + 1] == G[r + 1][c] == G[r + 1][c + 1]:
                    p += 3
        dark = sum(sum(1 for v in row if v) for row in G)
        p += abs(dark * 100 // (n * n) - 50) // 5 * 10
        return p
    if mask is not None:
        return build(mask)
    return min((build(m) for m in range(8)), key=penalty)


def svg(text, scale=6, border=4):
    G = make(text)
    n = len(G)
    size = (n + 2 * border) * scale
    rects = "".join(f'<rect x="{(c + border) * scale}" y="{(r + border) * scale}" width="{scale}" height="{scale}"/>'
                    for r in range(n) for c in range(n) if G[r][c])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><g fill="#000">{rects}</g></svg>')
