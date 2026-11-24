"""Interpretación de español coloquial por reglas (rápida, gratis y sin internet).

Entiende cantidades ("20 mil", "1.500.000", "3 millones", "veinte mil", "20 lucas"),
fechas ("hoy", "mañana", "el viernes", "10/03", "en 3 días", "15 de mayo"),
horas ("a las 8:30", "8 pm", "a las 8 de la noche") y repeticiones ("cada mes").

Todo lo que las reglas no entienden se delega al asistente conversacional (ai.py).
"""
from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from datetime import date, datetime, time, timedelta

Span = tuple[int, int]


def plano(s: str) -> str:
    """Minúsculas y sin tildes, conservando la longitud (para poder usar los mismos índices)."""
    return "".join(unicodedata.normalize("NFD", c.lower())[:1] for c in s)


def quitar_spans(texto: str, spans: list[Span | None]) -> str:
    validos = sorted(s for s in spans if s)
    out, pos = [], 0
    for a, b in validos:
        if a > pos:
            out.append(texto[pos:a])
        pos = max(pos, b)
    out.append(texto[pos:])
    return " ".join("".join(out).split())


def fmt_cop(n: int | float) -> str:
    return "$" + f"{int(round(n)):,}".replace(",", ".")


# ============================================================== CANTIDADES
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_RE_MESES = "|".join(MESES)

_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_DECENAS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90,
}
_CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
    "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800,
    "novecientos": 900,
}
_PALABRAS = {**_UNIDADES, **_DECENAS, **_CENTENAS}
_MULT = {
    "k": 1_000, "mil": 1_000, "luca": 1_000, "lucas": 1_000,
    "millon": 1_000_000, "millones": 1_000_000, "palo": 1_000_000, "palos": 1_000_000,
}

_NUM_RE = re.compile(
    r"(?<![\w/:\-.,])\$?\s?(\d[\d.,]*\d|\d)(?!\d)(?!\s*[:/\-]\d)"
    r"(?:\s*(k|mil|lucas?|millon(?:es)?|palos?)\b)?"
    r"(?!\s*(?:am|pm|a\.m|p\.m|dias?|semanas?|mes|meses|horas?|minutos?|h)\b)"
    rf"(?!\s+de\s+(?:{_RE_MESES})\b)"
)


def _numero(s: str) -> float:
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):  # 1.500.000 / 1,500
        return float(re.sub(r"[.,]", "", s))
    if re.fullmatch(r"\d+[.,]\d{1,2}", s):  # 20.5 / 1,5
        return float(s.replace(",", "."))
    return float(re.sub(r"[.,]", "", s))


def _palabras_a_numero(tokens: list[str]) -> int:
    total = actual = 0
    for t in tokens:
        if t == "y":
            continue
        if t in _PALABRAS:
            actual += _PALABRAS[t]
        elif t == "mil":
            total += (actual or 1) * 1_000
            actual = 0
        else:  # millon / millones
            total += (actual or 1) * 1_000_000
            actual = 0
    return total + actual


def buscar_cantidad(p: str) -> tuple[int | None, Span | None]:
    """Busca el monto en un texto ya normalizado con plano(). Devuelve (pesos, posición)."""
    mejor: tuple[float, Span] | None = None
    for m in _NUM_RE.finditer(p):
        v = _numero(m.group(1))
        if m.group(2):
            v *= _MULT[m.group(2)]
        if v > 0 and (mejor is None or v > mejor[0]):
            mejor = (v, m.span())
    if mejor:
        return int(round(mejor[0])), mejor[1]

    # Sin dígitos: "veinte mil", "dos millones quinientos mil"
    toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-z]+", p)]
    es_num = lambda t: t in _PALABRAS or t in ("mil", "millon", "millones")  # noqa: E731
    i, best = 0, None
    while i < len(toks):
        if es_num(toks[i][0]):
            j = i
            while j < len(toks) and (
                es_num(toks[j][0])
                or (toks[j][0] == "y" and j + 1 < len(toks) and es_num(toks[j + 1][0]))
            ):
                j += 1
            valor = _palabras_a_numero([t[0] for t in toks[i:j]])
            if valor >= 100 and (best is None or valor > best[0]):
                best = (valor, (toks[i][1], toks[j - 1][2]))
            i = j
        else:
            i += 1
    return (best[0], best[1]) if best else (None, None)


def extraer_cantidad(texto: str) -> int | None:
    return buscar_cantidad(plano(texto))[0]


# ================================================================== FECHAS
_PREF = r"(?:(?:para|el|este|proximo|dia)\s+)*"


def _sumar_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _fecha_valida(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def buscar_fecha(
    p: str, hoy: date, futuro: bool = False, pasado: bool = False
) -> tuple[date | None, Span | None]:
    """Busca una fecha en texto normalizado.

    futuro=True: fechas sin año / días de la semana apuntan hacia adelante (recordatorios, tareas).
    pasado=True: apuntan hacia atrás (movimientos: «el lunes gasté…» es el lunes que ya pasó).
    """
    # 2025-04-21
    m = re.search(r"(?<![\w/\-])(\d{4})-(\d{1,2})-(\d{1,2})(?![\w/\-])", p)
    if m and (d := _fecha_valida(int(m[1]), int(m[2]), int(m[3]))):
        return d, m.span()

    # 10/03 o 10/03/2026
    m = re.search(rf"(?<![\w/\-]){_PREF}(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{2,4}}))?(?![\w/])", p)
    if m:
        dia, mes = int(m[1]), int(m[2])
        anio = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else hoy.year
        d = _fecha_valida(anio, mes, dia)
        if d:
            if not m[3] and futuro and d < hoy:
                d = _fecha_valida(anio + 1, mes, dia) or d
            if not m[3] and pasado and d > hoy:
                d = _fecha_valida(anio - 1, mes, dia) or d
            return d, m.span()

    # 15 de mayo [de 2026]
    m = re.search(
        rf"{_PREF}\b(\d{{1,2}})\s+de\s+({_RE_MESES})(?:\s+(?:de|del)\s+(\d{{4}}))?\b", p
    )
    if m:
        anio = int(m[3]) if m[3] else hoy.year
        d = _fecha_valida(anio, MESES[m[2]], int(m[1]))
        if d:
            if not m[3] and futuro and d < hoy:
                d = _fecha_valida(anio + 1, MESES[m[2]], int(m[1])) or d
            if not m[3] and pasado and d > hoy:
                d = _fecha_valida(anio - 1, MESES[m[2]], int(m[1])) or d
            return d, m.span()

    # pasado mañana / mañana / hoy / ayer  (ojo: "en la mañana" NO es mañana-día)
    m = re.search(
        rf"{_PREF}(?:(pasado\s+manana)|(?<!la )(?<!esta )(manana)|(hoy)|(anteayer)|(ayer))\b", p
    )
    if m:
        delta = 2 if m[1] else 1 if m[2] else 0 if m[3] else -2 if m[4] else -1
        return hoy + timedelta(days=delta), m.span()

    # en 3 días / en una semana / en 2 meses
    m = re.search(r"\ben\s+(\d+|un|una|dos|tres|cuatro|cinco)\s+(dias?|semanas?|mes(?:es)?)\b", p)
    if m:
        n = int(m[1]) if m[1].isdigit() else _UNIDADES[m[1]]
        unidad = m[2]
        if unidad.startswith("dia"):
            return hoy + timedelta(days=n), m.span()
        if unidad.startswith("semana"):
            return hoy + timedelta(weeks=n), m.span()
        return _sumar_meses(hoy, n), m.span()

    # el viernes / proximo lunes
    m = re.search(rf"{_PREF}\b({'|'.join(DIAS)})s?\b", p)
    if m:
        if pasado:
            return hoy - timedelta(days=(hoy.weekday() - DIAS[m[1]]) % 7 or 7), m.span()
        delta = (DIAS[m[1]] - hoy.weekday()) % 7 or 7
        return hoy + timedelta(days=delta), m.span()

    # "el 15", "el día 6 de cada mes", "los 5 de cada mes" (día del mes)
    if futuro or pasado:
        m = re.search(
            rf"\bel\s+(?:dia\s+)?(\d{{1,2}})\b(?!\s*(?:[/:]|de\s+(?:{_RE_MESES})\b|\d|am|pm|mil|k\b))", p
        ) or re.search(r"\blos\s+(\d{1,2})\s+de\s+cada\s+mes\b", p)
        if m and 1 <= int(m[1]) <= 31:
            dia = int(m[1])
            d = _fecha_valida(hoy.year, hoy.month, dia)
            if d is None and dia > 28 and not pasado:  # «el 31» en un mes de 30 días → último día del mes
                d = _fecha_valida(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])
            if pasado:
                if d is None or d > hoy:
                    ant = _sumar_meses(hoy.replace(day=1), -1)
                    d = _fecha_valida(ant.year, ant.month, dia)
            elif d is None or d < hoy:
                sig = _sumar_meses(hoy.replace(day=1), 1)
                d = _fecha_valida(sig.year, sig.month, dia)
            if d:
                return d, m.span()
    return None, None


# =================================================================== HORAS
_AP = r"a\.?m\.?|p\.?m\.?|de\s+la\s+(?:manana|tarde|noche|madrugada)"
_HORA_RES = [
    re.compile(rf"\b(?:a|para)\s+las?\s+(?P<h>\d{{1,2}})(?:[:h](?P<m>\d{{2}}))?\s*(?P<ap>{_AP})?"),
    re.compile(rf"(?<![\w/\-])(?P<h>\d{{1,2}})[:h](?P<m>\d{{2}})\s*(?P<ap>{_AP})?"),
    re.compile(r"(?<![\w/\-])(?P<h>\d{1,2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?!\w)"),
]


def buscar_hora(p: str) -> tuple[time | None, Span | None]:
    for rx in _HORA_RES:
        m = rx.search(p)
        if not m:
            continue
        h = int(m["h"])
        mi = int(m.groupdict().get("m") or 0)
        ap = (m["ap"] or "").replace(".", "").replace(" ", "")
        if ("pm" in ap or "tarde" in ap or "noche" in ap) and h < 12:
            h += 12
        elif ("am" in ap or "manana" in ap or "madrugada" in ap) and h == 12:
            h = 0
        elif "noche" in ap and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return time(h, mi), m.span()
    return None, None


# ============================================================= REPETICIONES
_DIAS_RE = "|".join(DIAS)
_REP_DIA_SEMANA = re.compile(rf"\b(?:cada|todos\s+los|todas\s+las)\s+(?:{_DIAS_RE})s?\b")
_REP_INTERVALO = re.compile(
    r"\bcada\s+(?:(\d+|[a-z]+)\s+)?(horas?|minutos?|mins?)\b"
)
_REPS = [
    (re.compile(r"\b(?:cada|todos\s+los|todas\s+las)\s+mes(?:es)?\b|\bmensual(?:mente)?\b"), "mensual"),
    (re.compile(r"\b(?:cada|todos\s+los|todas\s+las)\s+semanas?\b|\bsemanal(?:mente)?\b"), "semanal"),
    (re.compile(r"\b(?:cada|todos\s+los|todas\s+las)\s+dias?\b|\bdiari[oa](?:mente)?\b"), "diario"),
]
MIN_INTERVALO = 5  # minutos: evita recordatorios que saturan el chat


def _cantidad_corta(txt: str | None) -> float | None:
    """'2' -> 2, 'dos' -> 2, 'media' -> 0.5, None -> 1 (cada hora)."""
    if txt is None:
        return 1
    if txt == "media":
        return 0.5
    if txt.isdigit():
        return int(txt)
    v = _PALABRAS.get(txt)
    return v if v is not None and v <= 100 else None


def buscar_repeticion(p: str) -> tuple[str | None, Span | None]:
    """'cada mes' -> 'mensual', 'todos los lunes' -> 'semanal', 'cada 2 horas' -> 'intervalo:120'."""
    for m in _REP_INTERVALO.finditer(p):
        n = _cantidad_corta(m.group(1))
        if n is None:
            continue
        minutos = int(round(n * (60 if m.group(2).startswith("hora") else 1)))
        return f"intervalo:{minutos}", m.span()
    m = _REP_DIA_SEMANA.search(p)
    if m:
        return "semanal", m.span()
    for rx, nombre in _REPS:
        m = rx.search(p)
        if m:
            return nombre, m.span()
    return None, None


_REL = re.compile(
    r"\b(?:en|dentro\s+de)\s+(\d+|[a-z]+)\s+(horas?|minutos?|mins?)\b(\s+y\s+media)?"
)


def buscar_relativo(p: str, ahora: datetime) -> tuple[datetime | None, Span | None]:
    """'en 30 minutos', 'dentro de 2 horas', 'en media hora' -> momento exacto."""
    for m in _REL.finditer(p):
        n = _cantidad_corta(m.group(1))
        if n is None:
            continue
        minutos = n * (60 if m.group(2).startswith("hora") else 1)
        if m.group(3):
            minutos += 30
        if minutos <= 0:
            continue
        return (ahora + timedelta(minutes=minutos)).replace(second=0, microsecond=0), m.span()
    return None, None


def minutos_intervalo(repeticion: str | None) -> int | None:
    if repeticion and repeticion.startswith("intervalo:"):
        try:
            return int(repeticion.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _mes_siguiente(d: datetime, ancla: int) -> datetime:
    y, m = d.year + d.month // 12, d.month % 12 + 1
    return datetime(y, m, min(ancla, monthrange(y, m)[1]), d.hour, d.minute)


def siguiente_fecha(dt: datetime, repeticion: str, despues_de: datetime) -> datetime:
    """Próxima ocurrencia de un recordatorio repetitivo, posterior a `despues_de`.

    «mensual:31» recuerda el día original (31) aunque febrero solo tenga 28: en marzo vuelve al 31.
    """
    if repeticion.startswith("intervalo:"):
        paso = lambda d: d + timedelta(minutes=max(MIN_INTERVALO, minutos_intervalo(repeticion) or 60))  # noqa: E731
    elif repeticion.startswith("mensual"):
        ancla = int(repeticion.split(":")[1]) if ":" in repeticion else dt.day
        paso = lambda d: _mes_siguiente(d, ancla)  # noqa: E731
    elif repeticion == "semanal":
        paso = lambda d: d + timedelta(weeks=1)  # noqa: E731
    else:
        paso = lambda d: d + timedelta(days=1)  # noqa: E731
    nuevo = paso(dt)
    while nuevo <= despues_de:
        nuevo = paso(nuevo)
    return nuevo


# ============================================================== CATEGORÍAS
_CATEGORIAS = {
    "comida": "comida almuerzo cena desayuno restaurante mercado supermercado exito jumbo pizza "
    "hamburguesa cafe snack onces domicilio rappi mecato fruta",
    "transporte": "transporte pasaje bus buses taxi uber didi cabify gasolina metro transmilenio "
    "peaje parqueadero moto sitp",
    "vivienda": "arriendo alquiler administracion hipoteca",
    "servicios": "luz agua gas internet celular telefono servicio recibo",
    "entretenimiento": "netflix spotify cine juego videojuego streaming fiesta concierto salida "
    "entretenimiento disney youtube",
    "salud": "medicina medicamento doctor farmacia drogueria eps odontologo salud gimnasio gym",
    "educacion": "universidad matricula libro curso papeleria colegio clase educacion",
    "ropa": "ropa camisa zapato pantalon tenis vestido chaqueta",
    "salario": "salario sueldo quincena nomina mesada",
    "ventas": "venta negocio",
    "freelance": "freelance independiente proyecto honorario",
    "regalo": "regalo",
}


def _sing(w: str) -> str:
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


_PALABRA_A_CAT = {_sing(w): cat for cat, ws in _CATEGORIAS.items() for w in ws.split()}


def normalizar_categoria(texto: str | None) -> str:
    """'Uber' -> 'transporte'; si no la conoce, devuelve lo escrito (minúsculas)."""
    if not texto or not texto.strip():
        return "otros"
    palabras = re.findall(r"[a-z]+", plano(texto))
    for w in palabras:
        if _sing(w) in _PALABRA_A_CAT:
            return _PALABRA_A_CAT[_sing(w)]
    return " ".join(texto.lower().split())[:40]


# ============================================================ MOVIMIENTOS
_ING = re.compile(
    r"\b(ingres\w*|recibi|recibio|recibimos|recibieron|recibido|me\s+pagaron|me\s+pago|me\s+pagan|"
    r"gane|ganamos|cobre|cobramos|me\s+llego|me\s+llegaron|me\s+consignaron|me\s+depositaron|"
    r"me\s+abonaron|me\s+dieron|me\s+regalaron|me\s+giraron|me\s+ingresaron|me\s+entro|me\s+entraron|"
    r"me\s+cayo|me\s+cayeron|vendi|vendimos|me\s+transfirieron|facture|recaude)\b"
)
_GAS = re.compile(
    r"\b(gast\w*|pagu\w*|pago|pague|compr\w*|invert\w*|me\s+cobraron|desembolse|debite|retire|"
    r"tanque\w*|recarg\w+|almorc\w*|cene|desayune|mercade|pedi|transferi|envie|consigne|gire|"
    r"me\s+cost\w+|costaron|costo|abone)\b"
)
_STOP = {
    "en", "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "mi", "mis", "por",
    "pesos", "peso", "cop", "a", "al", "con", "para", "me", "fue", "y", "que", "lo", "se", "hoy",
    "ayer", "mil", "millones", "millon", "e", "o", "como", "unos", "unas", "su", "mi", "es", "ya",
}
CATS_INGRESO = {"salario", "ventas", "freelance"}
MONTO_MINIMO = 100  # un "gasto" de $2 casi seguro es "2 pizzas", no pesos
_SEP = re.compile(r"\s*(?:,|;|\by\b|\bademas\b|\btambien\b)\s*")


def _movimiento(texto: str, hoy: date, tipo_defecto: str | None = None,
                fecha_defecto: date | None = None) -> dict | None:
    p = plano(texto)
    m_tipo = _ING.search(p)
    tipo = "ingreso" if m_tipo else None
    if not m_tipo:
        m_tipo = _GAS.search(p)
        tipo = "gasto" if m_tipo else None
    if not tipo:
        if not tipo_defecto:
            return None
        tipo = tipo_defecto
    monto, span_monto = buscar_cantidad(p)
    if not monto or monto < MONTO_MINIMO:
        return None
    fecha, span_fecha = buscar_fecha(p, hoy, pasado=True)
    resto = quitar_spans(p, [m_tipo.span() if m_tipo else None, span_monto, span_fecha])
    palabras = [w for w in re.findall(r"[a-z]+", resto) if w not in _STOP]
    categoria = "otros"
    if palabras:
        categoria = normalizar_categoria(" ".join(palabras))
        if categoria == " ".join(palabras)[:40]:
            categoria = " ".join(palabras[:2])
    return {
        "tipo": tipo,
        "monto": monto,
        "categoria": categoria,
        "sin_categoria": not palabras,
        "con_verbo": m_tipo is not None,
        "descripcion": " ".join(texto.split())[:200],
        "fecha": (fecha or fecha_defecto or hoy).isoformat(),
    }


def parse_movimiento(texto: str, hoy: date) -> dict | None:
    """'Gasté 20 mil en comida' -> {'tipo': 'gasto', 'monto': 20000, 'categoria': 'comida', ...}"""
    return _movimiento(texto, hoy)


def parse_movimientos(texto: str, hoy: date) -> list[dict]:
    """Varios movimientos en un mensaje: 'gasté 20 mil en comida y 15 mil en transporte'.

    Si no se puede separar con seguridad, devuelve el movimiento único (o lista vacía).
    """
    p = plano(texto)
    trozos, pos = [], 0
    for m in _SEP.finditer(p):
        if m.group().strip() == "y":  # «dos mil y quinientos» es UN monto, no dos movimientos
            antes, despues = re.findall(r"[a-z]+", p[:m.start()])[-1:], re.findall(r"[a-z]+", p[m.end():])[:1]
            if antes and despues and antes[0] in {*_PALABRAS, "mil", "millon", "millones"} \
                    and despues[0] in {*_PALABRAS, "medio", "media"}:
                continue
        trozos.append((pos, m.start()))
        pos = m.end()
    trozos.append((pos, len(p)))
    trozos = [t for t in trozos if p[t[0]:t[1]].strip()]
    if len(trozos) > 1:
        fecha_g = buscar_fecha(p, hoy, pasado=True)[0]
        movs, tipo = [], None
        for a, b in trozos:
            mv = _movimiento(texto[a:b], hoy, tipo, fecha_g)
            if mv is None or (not mv["con_verbo"] and mv["sin_categoria"]):
                movs = []
                break
            tipo = mv["tipo"]
            movs.append(mv)
        if len(movs) > 1:
            return movs
    uno = _movimiento(texto, hoy)
    return [uno] if uno else []


def parse_sin_verbo(texto: str, hoy: date) -> dict | None:
    """'salario 1500000', 'netflix 30000', 'uber 15 mil' -> movimiento (sin verbo, pero con categoría conocida)."""
    p = plano(texto)
    if _ING.search(p) or _GAS.search(p):
        return None
    monto, sp = buscar_cantidad(p)
    if not monto or monto < MONTO_MINIMO:
        return None
    fecha, sp_f = buscar_fecha(p, hoy, pasado=True)
    palabras = [w for w in re.findall(r"[a-z]+", quitar_spans(p, [sp, sp_f])) if w not in _STOP]
    if not 1 <= len(palabras) <= 3:
        return None
    cat = next((_PALABRA_A_CAT[_sing(w)] for w in palabras if _sing(w) in _PALABRA_A_CAT), None)
    if not cat or cat == "regalo":  # «regalo 50 mil» puede ser ingreso o gasto: mejor no adivinar
        return None
    return {
        "tipo": "ingreso" if cat in CATS_INGRESO else "gasto", "monto": monto, "categoria": cat,
        "sin_categoria": False, "con_verbo": False,
        "descripcion": " ".join(texto.split())[:200], "fecha": (fecha or hoy).isoformat(),
    }


# ============================================================ PERÍODOS
def _lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _rango_mes(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def buscar_periodo(p: str, hoy: date) -> tuple[str, str, str, Span] | None:
    """'esta semana', 'ayer', 'el mes pasado', 'en octubre', 'últimos 7 días' -> (desde, hasta, etiqueta, posición)."""
    def res(a: date, b: date, et: str, m: re.Match) -> tuple[str, str, str, Span]:
        return a.isoformat(), b.isoformat(), et, m.span()

    if m := re.search(r"\bhoy\b", p):
        return res(hoy, hoy, "hoy", m)
    if m := re.search(r"\bayer\b", p):
        return res(hoy - timedelta(days=1), hoy - timedelta(days=1), "ayer", m)
    if m := re.search(r"\bultimos?\s+(\d{1,3})\s+dias\b", p):
        n = max(1, int(m[1]))
        return res(hoy - timedelta(days=n - 1), hoy, f"los últimos {n} días", m)
    if m := re.search(r"\b(?:la\s+)?semana\s+pasada\b|\b(?:la\s+)?ultima\s+semana\b", p):
        lun = _lunes(hoy) - timedelta(days=7)
        return res(lun, lun + timedelta(days=6), "la semana pasada", m)
    if m := re.search(r"\b(?:esta|la)\s+semana\b", p):
        lun = _lunes(hoy)
        return res(lun, lun + timedelta(days=6), "esta semana", m)
    if m := re.search(r"\b(?:el\s+)?mes\s+pasado\b", p):
        ant = _sumar_meses(hoy.replace(day=1), -1)
        a, b = _rango_mes(ant.year, ant.month)
        return res(a, b, "el mes pasado", m)
    if m := re.search(rf"\b(?:(?:de|en|del|durante)\s+)?({_RE_MESES})\b", p):
        mes = MESES[m[1]]
        a, b = _rango_mes(hoy.year - (1 if mes > hoy.month else 0), mes)
        return res(a, b, f"de {m[1]}", m)
    if m := re.search(r"\b(?:este|el)\s+ano\b|\bdel\s+ano\b", p):
        return res(date(hoy.year, 1, 1), date(hoy.year, 12, 31), "este año", m)
    if m := re.search(r"\b(?:este|el)\s+mes\b|\bdel\s+mes\b|\bmensual\b", p):
        a, b = _rango_mes(hoy.year, hoy.month)
        return res(a, b, "este mes", m)
    return None


# ================================================== RECORDATORIOS Y TAREAS
_PREFIJO_REC = re.compile(
    r"^\s*(?:por\s+favor\s+)?(?:(?:crear?|crea|programa\w*|agenda\w*|pon\w*|anota\w*|haz\w*|activa\w*)\s+"
    r"(?:un\s+|una\s+|la\s+|el\s+)?)?(?:recordatorio|recuerdame|recuerdeme|avisame|recordarme|recordame|"
    r"recuerda|acuerdame|acordarme|alarma|no\s+me\s+dejes\s+olvidar)\s*(?:de\s+|que\s+|para\s+|:)?"
)
_PREFIJO_TAREA = re.compile(
    r"^\s*(?:por\s+favor\s+)?(?:"
    r"(?:(?:crear?|crea|agrega\w*|anadi\w*|anota\w*|apunta\w*|pon\w*|nueva)\s+)?(?:una\s+|la\s+)?tarea\s*(?::|de\s+|para\s+|que\s+)?"
    r"|(?:pendiente|por\s+hacer|todo)\s*[:\-]\s*"
    r"|(?:tengo\s+que|hay\s+que|me\s+toca|no\s+olvidar(?:me)?\s+(?:de\s+)?|debo)\s+"
    r")"
)
_COLAS = {"el", "para", "a", "de", "en", "y", "que", "las", "la", "los", "este", "proximo", "cada", "todos", "todas"}


def _limpiar_mensaje(orig: str, spans: list[Span | None]) -> str:
    msg = quitar_spans(orig, spans).strip(" ,.;:-¡!¿?")
    palabras = msg.split()
    while palabras and plano(palabras[-1]).strip(".,;:") in _COLAS:
        palabras.pop()
    return " ".join(palabras).strip(" ,.;:-")


def parse_recordatorio(texto: str, ahora: datetime, hora_defecto: time = time(9, 0)) -> dict | None:
    """Devuelve {'mensaje','fecha_hora','repeticion'}, {'error': ...} o None si falta cuándo."""
    p = plano(texto)
    m_pref = _PREFIJO_REC.match(p)
    rel, sp_rel = buscar_relativo(p, ahora)
    rep, sp_r = buscar_repeticion(p)
    fecha, sp_f = buscar_fecha(p, ahora.date(), futuro=True)
    hora, sp_h = buscar_hora(p)
    intervalo = minutos_intervalo(rep)
    if rel is not None:  # «en 30 minutos»
        dt, sp_f, sp_h = rel, None, None
    elif intervalo and fecha is None and hora is None:  # «cada 2 horas»: empieza dentro de un intervalo
        dt = (ahora + timedelta(minutes=intervalo)).replace(second=0, microsecond=0)
    else:
        if fecha is None and hora is None:
            return None
        if fecha is None:
            fecha = ahora.date() if datetime.combine(ahora.date(), hora) > ahora else ahora.date() + timedelta(days=1)
        dt = datetime.combine(fecha, hora or hora_defecto)
        if rep == "semanal" and dt - timedelta(days=7) > ahora:  # «todos los lunes» y hoy es lunes
            dt -= timedelta(days=7)
    if rep == "mensual":  # recuerda el día pedido (29-31) para meses cortos
        ancla = re.search(r"\b(\d{1,2})\s+de\s+cada\s+mes\b|\bel\s+(?:dia\s+)?(\d{1,2})\b", p)
        dia = int(ancla[1] or ancla[2]) if ancla else None
        if dia and 28 < dia <= 31:
            rep = f"mensual:{dia}"
    if intervalo is not None and intervalo < MIN_INTERVALO:
        return {"error": f"Lo mínimo es repetir cada {MIN_INTERVALO} minutos."}
    if dt <= ahora:
        return {"error": "esa fecha y hora ya pasaron"}
    mensaje = _limpiar_mensaje(texto, [m_pref.span() if m_pref else None, sp_f, sp_h, sp_r, sp_rel])
    if not mensaje:
        mensaje = "Alarma" if "alarma" in p else "Recordatorio"
    return {"mensaje": mensaje, "fecha_hora": dt, "repeticion": rep}


def mensaje_recordatorio(texto: str) -> str:
    """Texto del recordatorio sin el «recuérdame...» inicial (para continuar con preguntas guiadas)."""
    p = plano(texto)
    m = _PREFIJO_REC.match(p)
    _, sp_r = buscar_repeticion(p)
    return _limpiar_mensaje(texto, [m.span() if m else None, sp_r])


def parse_hora_simple(texto: str) -> time | None:
    """Hora escrita sola: '8', '8:30', '8 pm', '20:00', 'a las 3 de la tarde'."""
    h, _ = buscar_hora(plano(texto))
    if h:
        return h
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:h.](\d{2}))?\s*", plano(texto))
    if m and int(m[1]) <= 23 and int(m[2] or 0) <= 59:
        return time(int(m[1]), int(m[2] or 0))
    return None


def parse_tarea(texto: str, hoy: date) -> dict | None:
    p = plano(texto)
    m_pref = _PREFIJO_TAREA.match(p)
    fecha, sp_f = buscar_fecha(p, hoy, futuro=True)
    desc = _limpiar_mensaje(texto, [m_pref.span() if m_pref else None, sp_f])
    if not desc:
        return None
    return {"descripcion": desc, "fecha": fecha.isoformat() if fecha else None}


# ============================================================== PRESUPUESTOS
_IGNORAR_CAT = {"total", "todo", "todos", "general", "mes", "semana", "ano", "dia", "mensual", "mis", "cuentas",
                "plata", "dinero", "gastos", "ingresos", "movimientos", "presupuesto", "presupuestos",
                "limite", "tope", "maximo", "ponme", "pon", "fija", "define", "crear", "crea", "quiero",
                "tener", "un", "una", "sea", "es", "quitar", "borrar", "eliminar", "ver", "mi"}


def categoria_en_texto(p: str) -> str | None:
    """Categoría mencionada en un texto normalizado: conocida ('uber'→transporte) o la palabra tras en/de/para."""
    palabras = [w for w in re.findall(r"[a-z]+", p) if w not in _STOP and w not in _IGNORAR_CAT]
    for w in palabras:
        if _sing(w) in _PALABRA_A_CAT:
            return _PALABRA_A_CAT[_sing(w)]
    m = re.search(r"\b(?:en|de|para)\s+([a-z]{3,})\b", p)
    if m and m[1] not in _STOP and m[1] not in _IGNORAR_CAT:
        return m[1]
    return None


def detectar_presupuesto(p: str) -> dict | None:
    if not re.search(r"\b(?:presupuestos?|tope\s+de\s+gasto|limite\s+de\s+gasto)\b", p):
        return None
    if re.search(r"\b(?:quitar|borrar|eliminar|cancelar|sacar)\b", p):
        cat = categoria_en_texto(p)
        return {"intent": "borrar_presupuesto", "categoria": cat} if cat else {"intent": "ver_presupuestos"}
    monto, sp = buscar_cantidad(p)
    if not monto:
        return {"intent": "ver_presupuestos"}
    cat = categoria_en_texto(quitar_spans(p, [sp]))
    return {"intent": "presupuesto", "categoria": cat, "monto": monto} if cat else {"intent": "presupuesto_incompleto"}


# ============================================================== CONSULTAS
_RE_TOTAL = re.compile(
    r"^(?:y\s+)?cuanto\s+(?:dinero\s+|plata\s+)?(?:he\s+|llevo\s+|ya\s+|e\s+)?"
    r"(?P<v>gast\w+|ingres\w+|gane\b|ganado\b|recibi\b|recibido\b|cobre\b|cobrado\b)"
)
_RE_LISTA = re.compile(
    r"^(?:(?:ver|mis|muestra\w*|lista\w*|dame|consultar|mostrar|ensena\w*|quiero\s+ver|dime)\s+)?"
    r"(?:todos\s+)?(?:mis\s+|los\s+|el\s+|la\s+|las\s+)?"
    r"(?P<w>gastos|ingresos|movimientos|ultimos\s+movimientos|historial)\b"
)
_RE_RESUMEN = re.compile(
    r"^(?:ver\s+|dame\s+|muestra\w*\s+|quiero\s+(?:ver\s+)?)?(?:el\s+|un\s+|mi\s+)?(?:resumen|balance)\b"
    r"|^como\s+(?:voy|van\s+mis\s+(?:finanzas|cuentas|gastos))\b"
    r"|^cuanto\s+(?:me\s+queda|tengo|llevo\s+ahorrado)\b|^en\s+que\s+(?:gaste|gasto)\s+mas\b"
)


def detectar_consulta(p: str, hoy: date) -> dict | None:
    """Preguntas sobre los datos que se contestan sin IA: totales, listas y resúmenes por período."""
    per = buscar_periodo(p, hoy)
    sin_per = quitar_spans(p, [per[3]]) if per else p
    base = {"desde": per[0], "hasta": per[1], "periodo": per[2]} if per else {}
    if m := _RE_TOTAL.match(p):
        v = m["v"]
        tipo = "gasto" if v.startswith("gast") else "ingreso"
        cat = categoria_en_texto(quitar_spans(sin_per, [m.span()]))
        if not per:
            a, b = _rango_mes(hoy.year, hoy.month)
            base = {"desde": a.isoformat(), "hasta": b.isoformat(), "periodo": "este mes"}
        return {"intent": "consulta", "modo": "total", "tipo": tipo, "categoria": cat, **base}
    if m := _RE_LISTA.match(p):
        w = m["w"]
        tipo = "gasto" if w == "gastos" else "ingreso" if w == "ingresos" else None
        cat = categoria_en_texto(quitar_spans(sin_per, [m.span()]))
        return {"intent": "consulta", "modo": "lista", "tipo": tipo, "categoria": cat, **base}
    if _RE_RESUMEN.match(p):
        return {"intent": "resumen", **base}
    return None


# ================================================================== CHARLA
_SALUDOS = {"hola", "holi", "holis", "ola", "buenas", "buenos", "buen", "hey", "ey", "saludos", "buenaaas", "holaa"}
_RELLENO_SALUDO = {"bot", "helassist", "ai", "como", "estas", "esta", "que", "tal", "mas", "dias", "dia", "tardes",
                   "noches", "a", "todos", "amigo", "parce", "hermano", "y", "tu", "vas", "va", "todo", "bien",
                   "buenas", "buenos", "hola", "holaa", "qué"}


def detectar_charla(p: str) -> dict | None:
    t = re.sub(r"[^a-z ]", " ", p).split()
    if not t:
        return None
    if t[0] in _SALUDOS and len(t) <= 6 and all(w in _SALUDOS | _RELLENO_SALUDO for w in t):
        return {"intent": "saludo"}
    if re.match(r"^(?:muchas\s+|mil\s+)?gracias\b|^te\s+agradezco\b|^ok\s+gracias\b|^vale\s+gracias\b", p) and len(t) <= 6:
        return {"intent": "gracias"}
    if re.match(r"^(?:chao|chau|adios|hasta\s+luego|hasta\s+pronto|nos\s+vemos|bye)\b", p) and len(t) <= 5:
        return {"intent": "despedida"}
    if re.match(
        r"^(?:ayuda|help|instrucciones|comandos|que\s+(?:puedes|sabes|haces)(?:\s+hacer)?|"
        r"como\s+(?:funcionas|te\s+uso|se\s+usa|funciona)|quien\s+eres|para\s+que\s+sirves)\W*$", p
    ):
        return {"intent": "ayuda"}
    return None


# ============================================================== INTENCIONES
_RE_PREGUNTA = re.compile(
    r"^(que|como|cuanto|cuanta|cuantos|cuantas|cual|cuales|quien|donde|cuando|por que|porque|"
    r"puedes|podrias|sabes|me puedes|me ayudas|explica\w*|dime|recomienda\w*|ayudame|aconseja\w*)\b"
)


def es_pregunta(texto: str) -> bool:
    t = plano(texto).strip(" ¡!¿")
    return "?" in t or bool(_RE_PREGUNTA.match(t))


_TRIGGER_REC = re.compile(
    r"\b(recuerdame|recuerdeme|avisame|recordarme|recordame|recordatorio|acuerdame|acordarme|alarma|"
    r"no\s+me\s+dejes\s+olvidar)\b"
)


def detectar_intencion(texto: str, ahora: datetime, hora_defecto: time = time(9, 0)) -> dict | None:
    """Clasifica mensajes con estructura clara. Devuelve None si no está seguro (→ asistente IA)."""
    hoy, p = ahora.date(), plano(texto).strip()
    if not p:
        return None
    if re.match(r"^(reiniciar|resetear|borrar\s+todo|eliminar\s+todo)\b", p):
        return {"intent": "reiniciar"}
    if re.match(r"^(deshacer|borrar\s+(?:el\s+)?ultimo|eliminar\s+(?:el\s+)?ultimo)\b", p):
        return {"intent": "deshacer"}
    if charla := detectar_charla(p):
        return charla
    m = re.search(
        r"\b(?:completar|completa|completado|terminar|termine|finalizar|finalice|marcar|hecha|listo)\s+"
        r"(?:la\s+)?tarea\s+(?:numero\s+|n\s*°?\s*|#)?(\d+)",
        p,
    ) or re.search(r"\btarea\s+(?:numero\s+|#)?(\d+)\s+(?:completada|lista|hecha|terminada)", p)
    if m:
        return {"intent": "completar_tarea", "num": int(m[1])}
    m = re.search(r"\b(?:borrar|eliminar|cancelar|quitar)\s+(?:el\s+)?recordatorio\s+(?:numero\s+|#)?(\d+)", p)
    if m:
        return {"intent": "borrar_recordatorio", "num": int(m[1])}
    if re.search(r"\b(?:ver|mis|muestra\w*|lista\w*|cuales\s+son\s+mis|que)\s+(?:mis\s+)?(?:recordatorios|alarmas)\b", p):
        return {"intent": "ver_recordatorios"}
    if re.search(
        r"\b(?:ver|mis|muestra\w*|lista\w*|cuales\s+son\s+mis|que)\s+(?:mis\s+)?tareas\b|\btareas\s+pendientes\b|"
        r"\bque\s+tareas\s+tengo\b|\bmis\s+pendientes\b|\bque\s+tengo\s+pendiente\b|\blista\s+de\s+tareas\b", p
    ):
        return {"intent": "ver_tareas"}
    # «alarma» a secas aparece en preguntas ("¿cómo pongo una alarma?"); solo «recuérdame…» sirve como pregunta
    if (mt := _TRIGGER_REC.search(p)) and not (
        es_pregunta(texto) and mt[1] in ("alarma", "recordatorio") and not re.match(r"^(?:pon\w*|crea\w*|programa\w*)", p)
    ):
        r = parse_recordatorio(texto, ahora, hora_defecto)
        if r is None:
            return {"intent": "recordatorio_incompleto", "mensaje": mensaje_recordatorio(texto)}
        if "error" in r:
            if "mínimo" in r["error"]:
                return {"intent": "recordatorio_error", "error": r["error"]}
            return {"intent": "recordatorio_pasado", "mensaje": mensaje_recordatorio(texto)}
        return {"intent": "recordatorio", **r}
    if pr := detectar_presupuesto(p):
        return pr
    if re.search(r"\btarea\b", p) and re.match(
        r"^\s*(?:por\s+favor\s+)?(crear?|crea|agrega\w*|anadi\w*|anota\w*|apunta\w*|pon\w*|nueva)\b", p
    ) or re.match(r"^(?:tarea|pendiente|por\s+hacer|todo)\s*[:\-]", p) or (
        re.match(r"^(?:tengo\s+que|hay\s+que|me\s+toca|no\s+olvidar(?:me)?\s+(?:de\s+)?|debo)\s+(?!\$?\d)", p)
        and not es_pregunta(texto)
    ):
        t = parse_tarea(texto, hoy)
        return {"intent": "tarea", **t} if t else {"intent": "tarea_incompleta"}
    if re.match(r"^(ver\s+)?(el\s+)?analisis\b|^analiza\s+mis\s+finanzas|^cual\s+es\s+mi\s+mayor\s+gasto", p):
        return {"intent": "analisis"}
    if re.match(r"^(ver\s+)?(el\s+)?reporte(\s+mensual)?\b", p):
        return {"intent": "reporte"}
    if consulta := detectar_consulta(p, hoy):
        return consulta
    movs = parse_movimientos(texto, hoy)
    if not movs:
        sv = parse_sin_verbo(texto, hoy)
        movs = [sv] if sv else []
    if movs:
        return {"intent": "movimiento", **movs[0], "items": movs}
    return None
