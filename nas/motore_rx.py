# -*- coding: utf-8 -*-
"""
RAGGI X DAI BUCHI NERI - MOTORE (dati e calcoli, senza finestra)
================================================================
© 2026 Edoardo Battistel

Lo stesso file serve al programma sul PC e al servizio sul NAS. Il PC chiede i dati PRIMA al NAS (http://<NAS>:8097/dati/...);
se il NAS non risponde va direttamente agli archivi (MAXI, Fermi GBM, Swift/BAT).
Tutto ciò che scarica resta in una cache su disco.
"""
import contextlib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import numpy as np

VERSIONE = "1.1"
UTC = timezone.utc
UA = "RaggiX/1.1 (progetto personale di astronomia; Python urllib)"
MJD0 = datetime(1858, 11, 17, tzinfo=UTC)

MAXI = "https://maxi.riken.jp"
GBM = "https://batse.msfc.nasa.gov/gbm/science"
BAT = "https://swift.gsfc.nasa.gov/results/transients"
NAS_PREDEFINITO = "http://192.168.1.135:8097"
CONF = Path.home() / ".config" / "raggi_x.json"
CACHE = Path.home() / ".cache" / "raggi_x"

BAT_CRAB = 0.220          # 1 Crab nel Transient Monitor di Swift/BAT (15-50 keV), count/cm^2/s
ID_CRAB_MAXI = "J0534+220"
SWIFT_FINE_MJD = 61333    # 20/10/2026: ultima osservazione di Swift

# Sorgenti di partenza (le «preferite»): nome MAXI, nome comune, tipo
PREFERITE = [
    ("Cyg X-1", "Cyg X-1", "buco nero"),
    ("GX 339-4", "GX 339-4", "buco nero"),
    ("GRS 1915+105", "GRS 1915+105", "buco nero"),
    ("MAXI J1820+070", "MAXI J1820+070", "buco nero"),
    ("4U 1630-47", "4U 1630-47", "buco nero"),
    ("H 1743-322", "H 1743-322", "buco nero"),
    ("LMC X-1", "LMC X-1", "buco nero"),
    ("LMC X-3", "LMC X-3", "buco nero"),
    ("Cyg X-3", "Cyg X-3", "binaria X (buco nero?)"),
    ("Swift J1727.8-1613", "Swift J1727.8-1613", "buco nero"),
    ("Sco X-1", "Sco X-1", "stella di neutroni"),
    ("Aql X-1", "Aql X-1", "stella di neutroni"),
    ("Her X-1", "Her X-1", "pulsar X"),
    ("Cen X-3", "Cen X-3", "pulsar X"),
    ("Vela X-1", "Vela X-1", "pulsar X"),
    ("Mrk 421", "Mrk 421", "nucleo galattico (blazar)"),
    ("Crab", "Nebulosa del Granchio", "riferimento (candela)"),
]
_RICERCA_IPV4 = {"batse.msfc.nasa.gov", "gammaray.msfc.nasa.gov"}


def mjd_ora():
    return (datetime.now(UTC) - MJD0).total_seconds() / 86400.0


def mjd_a_data(mjd):
    return (MJD0 + timedelta(days=float(mjd))).strftime("%d/%m/%Y")


def leggi_conf():
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salva_conf(c):
    CONF.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CONF)


# =============================================================================
# RETE
# =============================================================================
_lock_ipv4 = threading.Lock()


@contextlib.contextmanager
def solo_ipv4(host):
    """Per i server della NASA di Huntsville (Fermi GBM) la connessione impiegava ~40 s:
    probabile IPv6 che non funziona. Qui si chiede solo l'indirizzo IPv4."""
    if host not in _RICERCA_IPV4:
        yield
        return
    with _lock_ipv4:
        originale = socket.getaddrinfo

        def solo4(h, *a, **k):
            if h == host:
                a = list(a)
                if len(a) >= 2:
                    a[1] = socket.AF_INET
                else:
                    k["family"] = socket.AF_INET
            return originale(h, *a, **k)

        socket.getaddrinfo = solo4
        try:
            yield
        finally:
            socket.getaddrinfo = originale


def http_get(url, tempo_max=30, se_modificato_dopo=None, forza_ipv4=True):
    """Ritorna (codice, testo_bytes, last_modified). 304 = non cambiato."""
    h = {"User-Agent": UA}
    if se_modificato_dopo:
        h["If-Modified-Since"] = se_modificato_dopo
    req = urllib.request.Request(url, headers=h)
    host = urllib.request.urlparse(url).hostname or ""
    ctx = solo_ipv4(host) if forza_ipv4 else contextlib.nullcontext()
    with ctx:
        try:
            with urllib.request.urlopen(req, timeout=tempo_max) as r:
                return r.status, r.read(), r.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, b"", se_modificato_dopo
            raise


class Fonte:
    """Prende i file dal NAS se c'è, altrimenti dagli archivi; tiene una cache su disco."""

    def __init__(self, nas=None, cache=CACHE, registro=print, usa_nas=True):
        self.nas = (nas or leggi_conf().get("nas") or NAS_PREDEFINITO).rstrip("/")
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.registro = registro
        self.nas_attivo = None if usa_nas else False   # None = non ancora provato
        self.ultimo_esito = ""          # "ok", "cache", "assente" (404) o "rete" (archivio che non risponde)
        self.provenienza = {}           # chiave -> "NAS" / "archivio" / "cache"
        self._lock = threading.Lock()

    def _prova_nas(self):
        if self.nas_attivo is None:
            try:
                cod, testo, _ = http_get(self.nas + "/dati/stato.json", tempo_max=3)
                self.nas_attivo = cod == 200 and testo.strip().startswith(b"{")
            except Exception:
                self.nas_attivo = False
            self.registro("Servizio sul NAS: " + ("attivo, uso i suoi dati" if self.nas_attivo
                                                  else "non ancora attivo, vado direttamente agli archivi"))
        return self.nas_attivo

    def prendi(self, chiave, url_archivio, eta_max_ore=3.0, tempo_max=30, tentativi=2):
        """chiave = percorso relativo (es. 'maxi/J1958+352_1day.dat'). Ritorna bytes o None."""
        f = self.cache / chiave
        meta = f.with_name(f.name + ".meta")
        m = {}
        if f.exists():
            try:
                m = json.loads(meta.read_text())
            except Exception:
                m = {}
            if time.time() - m.get("preso", 0) < eta_max_ore * 3600:
                self.provenienza[chiave] = "cache"
                self.ultimo_esito = "cache"
                return f.read_bytes()
        sorgenti = []
        if self._prova_nas():
            sorgenti.append(("NAS", self.nas + "/dati/" + chiave, 10))
        sorgenti.append(("archivio", url_archivio, tempo_max))
        for nome, url, tmax in sorgenti:
            errore = "404"
            for t in range(tentativi):
                try:
                    cod, dati, lm = http_get(url, tmax, m.get("last_modified") if f.exists() else None)
                    if cod == 304:
                        dati = f.read_bytes()
                    elif not dati or dati.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")) and not url.endswith(".html"):
                        raise ValueError("risposta HTML al posto dei dati")
                    f.parent.mkdir(parents=True, exist_ok=True)
                    if cod != 304:
                        tmp = f.with_name(f.name + ".tmp")
                        tmp.write_bytes(dati)
                        tmp.replace(f)
                    meta.write_text(json.dumps({"preso": time.time(), "last_modified": lm, "da": nome, "url": url}))
                    self.provenienza[chiave] = nome
                    self.ultimo_esito = "ok"
                    return dati
                except urllib.error.HTTPError as e:
                    errore = f"HTTP {e.code}"
                    if e.code == 404:
                        break                      # non esiste: inutile riprovare
                except Exception as e:
                    errore = f"{type(e).__name__}: {e}"[:120]
                if t + 1 < tentativi:
                    time.sleep(3)
            if nome == "archivio":
                # 404 = non esiste; altro codice HTTP = il server risponde ma ha un problema; niente risposta = rete
                self.ultimo_esito = "assente" if errore == "HTTP 404" else \
                    "http" if errore.startswith("HTTP") else "rete"
                if errore != "HTTP 404":
                    self.registro(f"{chiave}: non disponibile ({errore})")
        if f.exists():                              # meglio vecchio che niente
            self.provenienza[chiave] = "cache (vecchia)"
            return f.read_bytes()
        return None


# =============================================================================
# LETTORI DEI FORMATI (verificati sui file veri del 17/11/2026)
# =============================================================================
def leggi_elenco_maxi(html):
    """slist.html -> lista di dict(nome, ra, dec, id)."""
    testo = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    out = []
    for m in re.finditer(r"<tr><td>([^<]+)</td><td[^>]*>\s*([-\d.]+),\s*([-\d.]+)\s*</td><td[^>]*>"
                         r"<a href=\"\.\./star_data/(J\d{4}[+-]\d{3})/", testo):
        # MAXI a volte attacca un commento al nome: «GX 354-0_Slow-Burster with Rapid-Burster»
        grezzo = m.group(1).strip()
        nome, _, nota = grezzo.partition("_")
        out.append({"nome": nome.strip(), "ra": float(m.group(2)), "dec": float(m.group(3)),
                    "id": m.group(4), "nota": nota.replace("-", " ").strip()})
    return out


# Note per sorgenti che MAXI non separa bene (la sua vista è larga circa 1,5°)
NOTE_SORGENTI = {
    "GX 354-0": "Attenzione: è lo «Slow Burster», e MAXI non lo separa bene dal Rapid Burster "
                "(H 1730-333 / MXB 1730-335), a 1,5°, che si accende per qualche settimana ogni 80–105 giorni. "
                "Un aumento improvviso qui è quasi sempre il Rapid Burster.",
    "H 1730-333": "È il «Rapid Burster»: si accende per qualche settimana ogni 80–105 giorni. "
                  "MAXI lo mescola in parte con GX 354-0 (lo Slow Burster), a 1,5°: le due curve si somigliano.",
}


def _numeri(testo, ncol_min):
    righe = []
    for r in (testo.decode("utf-8", "replace") if isinstance(testo, bytes) else testo).splitlines():
        s = r.split()
        if len(s) < ncol_min or s[0][:1] in "#!":
            continue
        try:
            righe.append([float(x) for x in s[:ncol_min]])
        except ValueError:
            continue
    return np.array(righe, dtype=float).reshape(-1, ncol_min)


def leggi_maxi(testo):
    """9 colonne: MJD, 2-20 keV, err, 2-4, err, 4-10, err, 10-20, err (ph/s/cm2)."""
    a = _numeri(testo, 9)
    return {"t": a[:, 0], "f": a[:, 1], "e": a[:, 2], "f24": a[:, 3], "e24": a[:, 4],
            "f410": a[:, 5], "e410": a[:, 6], "f1020": a[:, 7], "e1020": a[:, 8]}


def leggi_gbm(testo):
    """11 colonne: tstart, tstop, poi flusso/err in 12-25, 25-50, 50-100, 100-300 keV (mCrab), passi.
    La riga «Long-term average:» comincia con una parola e viene saltata da sola."""
    a = _numeri(testo, 11)
    return {"t": 0.5 * (a[:, 0] + a[:, 1]), "f1225": a[:, 2], "e1225": a[:, 3], "f2550": a[:, 4],
            "e2550": a[:, 5], "f50100": a[:, 6], "e50100": a[:, 7], "passi": a[:, 10]}


def leggi_bat(testo):
    """11 colonne: TIME (MJD), RATE, ERROR (count/cm2/s, 15-50 keV), ..., DATA_FLAG (col. 8)."""
    a = _numeri(testo, 8)
    buoni = a[:, 7] == 0
    return {"t": a[buoni, 0], "f": a[buoni, 1] / BAT_CRAB * 1000, "e": a[buoni, 2] / BAT_CRAB * 1000}


def leggi_elenco_gbm(html):
    testo = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    return sorted(set(re.findall(r"earth_occ/([A-Za-z0-9_+\-.]+)\.html", testo)))


def nome_compatto(nome):
    """'Cyg X-1' -> 'CYGX-1' (stile GBM); per Swift/BAT 'CygX-1'."""
    return re.sub(r"\s+", "", nome)


# =============================================================================
# CALCOLI
# =============================================================================
def media_pesata(f, e):
    ok = np.isfinite(f) & np.isfinite(e) & (e > 0)
    if not ok.any():
        return np.nan, np.nan
    w = 1.0 / e[ok] ** 2
    return float(np.sum(w * f[ok]) / np.sum(w)), float(1.0 / np.sqrt(np.sum(w)))


def crab_maxi(dati_crab):
    """Flusso della Crab in MAXI 2-20 keV (ph/s/cm2) = 1000 mCrab: mediana degli ultimi 2 anni."""
    t = dati_crab["t"]
    sel = t > t.max() - 730
    v = np.median(dati_crab["f"][sel]) if sel.sum() >= 20 else np.nan
    return float(v) if np.isfinite(v) and v > 0 else None


GIORNI_DATI_VECCHI = 14      # MAXI non osserva tutte le sorgenti ogni giorno (Her X-1, Mrk 421: 10 giorni di vuoto)

STATI = {
    "nuova": "NUOVA ESPLOSIONE",
    "esplosione": "in esplosione",
    "aumento": "in aumento",
    "calo": "in calo",
    "calma": "calma",
    "debole": "troppo debole",
    "vecchi": "dati vecchi",
    "pochi": "pochi dati",
}


def stato_sorgente(d, scala_mcrab=None, giorni_recenti=3, soglia_x=3.0, soglia_sigma=5.0):
    """Confronta gli ultimi giorni con il livello abituale (mediana di un anno, esclusi gli ultimi 14 giorni)."""
    t, f, e = d["t"], d["f"], d["e"]
    ok = np.isfinite(f) & np.isfinite(e) & (e > 0)
    t, f, e = t[ok], f[ok], e[ok]
    k = scala_mcrab or 1.0
    r = {"ultimo_mjd": float(t.max()) if len(t) else np.nan, "unita": "mCrab" if scala_mcrab else "ph/s/cm²"}
    if len(t) < 30:
        r["stato"] = "pochi"
        return r
    adesso = t.max()
    rec = t > adesso - giorni_recenti
    base = (t > adesso - 365) & (t < adesso - 14)
    if base.sum() < 20:
        base = t < adesso - 14
    oggi, err_oggi = media_pesata(f[rec], e[rec])
    abituale = float(np.median(f[base]))
    dispersione = float(1.4826 * np.median(np.abs(f[base] - abituale)))
    rumore = float(np.median(e[base]))
    sigma = np.hypot(max(dispersione, rumore), err_oggi)
    z = (oggi - abituale) / sigma if sigma > 0 else 0.0
    rapporto = oggi / abituale if abituale > max(rumore, 1e-9) else np.inf if oggi > 3 * rumore else np.nan
    r.update(oggi=oggi * k, err_oggi=err_oggi * k, abituale=abituale * k, dispersione=dispersione * k,
             z=float(z), rapporto=float(rapporto) if np.isfinite(rapporto) else rapporto)
    r["giorni_senza_dati"] = float(mjd_ora() - adesso)
    if mjd_ora() - adesso > GIORNI_DATI_VECCHI:
        r["stato"] = "vecchi"
    elif oggi < 3 * err_oggi and abituale < 3 * rumore and z < soglia_sigma:
        r["stato"] = "debole"
    elif z >= soglia_sigma and (rapporto >= soglia_x or not np.isfinite(rapporto)):
        r["stato"] = "esplosione"
    elif z >= 3 and rapporto >= 1.5:
        r["stato"] = "aumento"
    elif z <= -3 and rapporto <= 0.67:
        r["stato"] = "calo"
    else:
        r["stato"] = "calma"
    # durezza (4-10 / 2-4 keV) recente e abituale
    r["durezza_oggi"] = durezza(d, (adesso - 7, adesso + 1))
    r["durezza_abituale"] = durezza(d, (adesso - 365, adesso - 14))
    if r["stato"] in ("esplosione", "aumento"):
        r["esplosione"] = esplosione_in_corso(d, abituale, max(dispersione, rumore), k)
        if r["stato"] == "esplosione" and r["esplosione"] and r["esplosione"]["nuova"]:
            r["stato"] = "nuova"
    return r


def esplosione_in_corso(d, abituale, sigma, k=1.0, soglia_sigma=3.0, buco_max=10, giorni_nuova=21):
    """Da quando dura l'esplosione attuale: medie di 3 giorni sopra abituale + 3 sigma,
    a ritroso dall'ultimo dato, accettando buchi nei dati fino a 10 giorni.
    Ritorna dict(inizio, picco, data_picco, giorni, nuova) o None."""
    t, f, e = d["t"], d["f"], d["e"]
    ok = np.isfinite(f) & np.isfinite(e) & (e > 0)
    sel = ok & (t > t.max() - 1500)
    tb, fb, eb = ribinna(t[sel], f[sel], e[sel], 3)
    if len(tb) == 0:
        return None
    sopra = fb - 2 * eb > abituale + soglia_sigma * sigma
    i = len(tb) - 1
    while i >= 0 and not sopra[i]:
        i -= 1
    if i < 0 or tb[-1] - tb[i] > buco_max:
        return None
    fine = inizio = i
    for j in range(i - 1, -1, -1):
        if tb[inizio] - tb[j] > buco_max:
            break
        if sopra[j]:
            inizio = j
    ip = inizio + int(np.argmax(fb[inizio:fine + 1]))
    t0 = float(tb[inizio] - 1.5)
    return {"inizio_mjd": t0, "inizio": mjd_a_data(t0), "picco": float(fb[ip] * k),
            "data_picco": mjd_a_data(tb[ip]), "giorni": float(t.max() - t0),
            "nuova": bool(mjd_ora() - t0 <= giorni_nuova)}


def serie_durezza(d, snr_min=3.0):
    """Rapporto di durezza 4-10 keV / 2-4 keV, giorno per giorno, solo dove entrambe le bande sono misurate."""
    ok = (d["f24"] > snr_min * d["e24"]) & (d["f410"] > snr_min * d["e410"]) & (d["e24"] > 0)
    hr = d["f410"][ok] / d["f24"][ok]
    ehr = hr * np.hypot(d["e410"][ok] / d["f410"][ok], d["e24"][ok] / d["f24"][ok])
    return d["t"][ok], hr, ehr, ok


def durezza(d, finestra):
    t, hr, ehr, _ = serie_durezza(d)
    sel = (t >= finestra[0]) & (t <= finestra[1])
    return float(np.median(hr[sel])) if sel.sum() >= 2 else np.nan


def ribinna(t, f, e, giorni):
    """Medie pesate su intervalli di 'giorni' (per le viste lunghe)."""
    if giorni <= 1 or len(t) == 0:
        return t, f, e
    k = np.floor((t - t.min()) / giorni).astype(int)
    tt, ff, ee = [], [], []
    for i in np.unique(k):
        s = k == i
        m, em = media_pesata(f[s], e[s])
        if np.isfinite(m):
            tt.append(t[s].mean())
            ff.append(m)
            ee.append(em)
    return np.array(tt), np.array(ff), np.array(ee)


def spiegazione(nome, tipo, st, gbm=None, nota=""):
    """Testo in italiano per la finestra e per la pagina."""
    testo = _spiegazione(nome, tipo, st, gbm)
    extra = NOTE_SORGENTI.get(nome) or (f"Nota di MAXI su questa sorgente: «{nota}»." if nota else "")
    return testo + ("\n" + extra if extra else "")


def _spiegazione(nome, tipo, st, gbm=None):
    if "oggi" not in st:
        return f"{nome}: {STATI.get(st.get('stato'), '?')}. MAXI ha meno di 30 giorni di misure utili."
    u = st["unita"]
    righe = [f"{nome} ({tipo}). Ultima misura MAXI: {mjd_a_data(st['ultimo_mjd'])}."]
    n = 0 if u == "mCrab" else 3
    righe.append(f"Negli ultimi 3 giorni brilla {st['oggi']:.{n}f} ± {st['err_oggi']:.{n}f} {u} (2–20 keV); "
                 f"di solito, nell'ultimo anno, {st['abituale']:.{n}f} {u}.")
    rap = st["rapporto"]
    if isinstance(rap, float) and np.isfinite(rap):
        righe.append(f"È {rap:.1f} volte il livello abituale ({st['z']:+.1f} volte la sua variabilità normale).")
    s = st["stato"]
    if s in ("esplosione", "nuova"):
        es = st.get("esplosione")
        if s == "nuova":
            righe.append("→ È UNA NUOVA ESPLOSIONE: il disco di gas attorno all'oggetto compatto si è appena acceso.")
        else:
            righe.append("→ È in esplosione: il disco di gas attorno all'oggetto compatto è acceso.")
        if es:
            righe.append(f"L'esplosione è cominciata verso il {es['inizio']} ({es['giorni']:.0f} giorni fa); "
                         f"picco di {es['picco']:.0f} {u} il {es['data_picco']}.")
    elif s == "aumento":
        righe.append("→ Sta crescendo più del normale: da tenere d'occhio, può essere l'inizio di un'esplosione.")
    elif s == "calo":
        righe.append("→ È più debole del solito: un'esplosione si sta spegnendo o la sorgente è in una fase bassa.")
    elif s == "debole":
        righe.append("→ È troppo debole per MAXI: ora non si vede (è «in quiete»).")
    elif s == "vecchi":
        righe.append(f"→ MAXI non ha misure nuove da {st.get('giorni_senza_dati', 0):.0f} giorni "
                     "(non osserva tutte le sorgenti ogni giorno: succede).")
    else:
        righe.append("→ È nel suo stato abituale.")
    ho, hb = st.get("durezza_oggi", np.nan), st.get("durezza_abituale", np.nan)
    if np.isfinite(ho) and np.isfinite(hb):
        conf = "più DURO del solito (più raggi X energetici)" if ho > 1.25 * hb else \
            "più MORBIDO del solito (domina il disco caldo)" if ho < 0.8 * hb else "simile al solito"
        righe.append(f"Rapporto di durezza 4–10/2–4 keV: {ho:.2f} (di solito {hb:.2f}): spettro {conf}.")
    if gbm is not None and len(gbm["t"]):
        sel = gbm["t"] > gbm["t"].max() - 7
        m, em = media_pesata(gbm["f2550"][sel], gbm["e2550"][sel])
        if np.isfinite(m):
            righe.append(f"Fermi GBM (25–50 keV, ultima settimana fino al {mjd_a_data(gbm['t'].max())}): "
                         f"{m:.0f} ± {em:.0f} mCrab.")
    return "\n".join(righe)


# =============================================================================
# GRAFICO (testi in inglese, come tutti i risultati scientifici)
# =============================================================================
PERIODI = {"60 giorni": 60, "1 anno": 365, "5 anni": 1826, "tutto": None}


def disegna(fig, nome, d, scala, gbm=None, bat=None, giorni=365, colori=None):
    """Tre pannelli: curva di luce, durezza nel tempo, diagramma durezza-intensità."""
    import matplotlib.dates as mdates
    fig.clear()
    k = scala or 1.0
    u = "mCrab" if scala else "ph s$^{-1}$ cm$^{-2}$"
    fine = d["t"].max() + 1
    inizio = fine - giorni if giorni else d["t"].min() - 1
    bin_ = 1 if not giorni or giorni <= 400 else (7 if giorni <= 2000 else 14)
    if not giorni:
        bin_ = 14

    def date(t):
        return [MJD0 + timedelta(days=float(x)) for x in t]

    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1], width_ratios=[1.6, 1], hspace=0.38, wspace=0.28,
                          left=0.08, right=0.96, top=0.93, bottom=0.10)
    a1 = fig.add_subplot(gs[0, :])
    a2 = fig.add_subplot(gs[1, 0], sharex=a1)
    a3 = fig.add_subplot(gs[1, 1])

    s = (d["t"] >= inizio)
    t, f, e = ribinna(d["t"][s], d["f"][s] * k, d["e"][s] * k, bin_)
    a1.errorbar(date(t), f, yerr=e, fmt="o", ms=2.5, lw=0.6, color="#1f5fbf",
                label=f"MAXI 2–20 keV" + (f" ({bin_}-day bins)" if bin_ > 1 else " (daily)"))
    if gbm is not None and len(gbm["t"]):
        sg = gbm["t"] >= inizio
        tg, fg, eg = ribinna(gbm["t"][sg], gbm["f2550"][sg], gbm["e2550"][sg], max(bin_, 1))
        if len(tg):
            a1.errorbar(date(tg), fg, yerr=eg, fmt="s", ms=2.5, lw=0.6, color="#d9822b", alpha=0.85,
                        label="Fermi GBM 25–50 keV (Earth occultation)")
    if bat is not None and len(bat["t"]):
        sb = bat["t"] >= inizio
        if sb.any():
            tb, fb, eb = ribinna(bat["t"][sb], bat["f"][sb], bat["e"][sb], max(bin_, 1))
            a1.errorbar(date(tb), fb, yerr=eb, fmt="^", ms=2.5, lw=0.6, color="#7a4fb5", alpha=0.8,
                        label="Swift/BAT 15–50 keV (mission ended 2026-10-20)")
    a1.set_ylabel(f"Flux ({u})" if scala else f"Flux ({u})")
    a1.set_title(f"{nome} — X-ray light curve", loc="left", fontweight="bold")
    a1.legend(loc="upper left", fontsize="small", framealpha=0.85)
    a1.grid(alpha=0.3)

    th, hr, ehr, okh = serie_durezza(d)
    sh = th >= inizio
    if sh.any():
        a2.errorbar(date(th[sh]), hr[sh], yerr=ehr[sh], fmt="o", ms=2.5, lw=0.5, color="#2a9d8f")
    a2.set_ylabel("Hardness\n(4–10 / 2–4 keV)")
    a2.set_title("Hardness ratio", loc="left")
    a2.grid(alpha=0.3)
    a2.xaxis.set_major_formatter(mdates.ConciseDateFormatter(a2.xaxis.get_major_locator()))
    a1.tick_params(labelbottom=False)

    # diagramma durezza-intensità: colore = tempo
    tt = d["t"][okh]
    ff = d["f"][okh] * k
    sel = tt >= inizio
    if sel.sum() >= 2:
        sc = a3.scatter(hr[sel], ff[sel], c=tt[sel] - tt[sel].max(), cmap="viridis", s=10)
        a3.plot(hr[sel][-1:], ff[sel][-1:], "*", ms=14, color="#e63946", mec="k", label="latest")
        cb = fig.colorbar(sc, ax=a3, pad=0.02)
        cb.set_label("days before latest")
        a3.legend(loc="best", fontsize="small")
        if ff[sel].min() > 0 and ff[sel].max() / ff[sel].min() > 8:
            a3.set_yscale("log")
    else:
        a3.text(0.5, 0.5, "too faint for\nhardness ratio", ha="center", va="center", transform=a3.transAxes)
    a3.set_xlabel("Hardness (4–10 / 2–4 keV)")
    a3.set_ylabel(f"2–20 keV ({u})")
    a3.set_title("Hardness–intensity", loc="left")
    a3.grid(alpha=0.3)
    fig.text(0.995, 0.005, "© 2026 Edoardo Battistel · data: MAXI (RIKEN/JAXA), Fermi GBM (NASA/MSFC), Swift/BAT",
             ha="right", va="bottom", fontsize="x-small", color="0.4")


# =============================================================================
# TIPO DI SORGENTE, NOMI, NOTIZIE (ATel, GCN)
# =============================================================================
def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def tipo_da_nome(nome):
    """Tipo della sorgente: dalle preferite, altrimenti dal nome (indicativo)."""
    for n, _, tipo in PREFERITE:
        if norm(n) == norm(nome):
            return tipo
    n = (nome or "").strip()
    if re.match(r"(EP ?\d{6}|EP J|AT ?20\d\d|GRB|Swift J\d{6}\.\d|MAXI J\d{4}[+-]\d{3}$)", n) and \
            not any(norm(x) == norm(n) for x, _, _ in PREFERITE):
        return "transiente" if n.startswith(("EP", "AT", "GRB")) else "binaria X (transiente)"
    if re.match(r"(Mrk|NGC|3C|PKS|1ES|Cen A|M ?87|IC|ESO|MCG|Fairall|H ?1426|BL Lac|4C|QSO|Ark|IRAS|2MASX|1H|"
                r"LEDA|UGC|SWIFT J|PG ?\d|RBS)", n, re.I):
        return "galassia attiva"
    if re.search(r"cluster|Coma|Perseus|Virgo|Abell|A\d{3,4}|ACO|CIZA|ClG", n, re.I):
        return "ammasso di galassie"
    if re.match(r"(SN|Cas A|Tycho|Kepler|Pup A|Vela SNR|Cygnus Loop|W ?\d)", n, re.I):
        return "resto di supernova"
    if re.search(r"(Capella|Algol|HR ?\d|UX Ari|AR Lac|Ori|II Peg|σ|sigma|CI Cam|eta Car|Eta Car)", n):
        return "stella"
    if re.match(r"(MAXI|Swift|XTE|GRS|GRO|GX|4U|1A|EXO|KS|SAX|IGR|Aql|Sco|Cyg|Cir|Ser|Sgr|Nor|Ara|LMC|SMC|"
                r"H ?\d|A ?\d|X ?\d|Vela X|Cen X|Her X|GS|MXB|XB|RX|1RXS|2S|3A|SLX|Rapid|Terzan|Oph)", n):
        return "binaria X"
    return "sorgente X"


# Altri nomi con cui le notizie (ATel, GCN) citano alcune sorgenti
ALTRI_NOMI = {
    "H 1730-333": ["MXB 1730-335", "Rapid Burster", "Liller 1"],
    "GX 354-0": ["4U 1728-34", "Slow Burster"],
    "LS V +44 17": ["RX J0440.9+4431", "LSV+44 17"],
    "Cyg X-1": ["HDE 226868"], "Sco X-1": ["4U 1617-155"], "Her X-1": ["HZ Her"],
    "Aql X-1": ["V1333 Aql"], "Cen X-3": ["V779 Cen"], "Vela X-1": ["GP Vel", "4U 0900-40"],
    "GRS 1915+105": ["V1487 Aql"], "4U 1630-47": ["4U 1630-472"], "H 1743-322": ["IGR J17464-3213"],
    "MAXI J1820+070": ["ASASSN-18ey"], "Crab": ["Crab Nebula", "Crab pulsar", "PSR B0531+21"],
}


def nomi_di_ricerca(nome):
    """Forme normalizzate del nome da cercare nei titoli delle notizie ('H 1743-322' -> 'h1743322')."""
    n = norm(nome)
    forme = {n} if len(n) >= 5 else set()
    forme |= {norm(x) for x in ALTRI_NOMI.get(nome, [])}
    if nome.lower().startswith("crab"):
        forme |= {"crab"}
    m = re.match(r"(MAXI|Swift|XTE|SAX|IGR|EXO|GRS|GRO)\s*J?(\d{4}\.?\d*[+-]\d{2,4})", nome)
    if m:
        forme.add(norm(m.group(2)))          # 'J1820+070' scritto senza prefisso
    return {f for f in forme if len(f) >= 4}


def citata_in(nome, testo):
    t = norm(testo)
    return any(f in t for f in nomi_di_ricerca(nome))


def leggi_atel(rss):
    """RSS di ATel -> lista di dict(id, titolo, link, data, testo, fonte)."""
    testo = rss.decode("utf-8", "replace") if isinstance(rss, bytes) else rss
    out = []
    for voce in re.findall(r"<item\b.*?</item>", testo, re.S | re.I):
        def campo(nome):
            m = re.search(rf"<{nome}[^>]*>(.*?)</{nome}>", voce, re.S | re.I)
            v = m.group(1) if m else ""
            return re.sub(r"\s+", " ", re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", " ", v)).strip()
        link = campo("link")
        m = re.search(r"read=(\d+)", link)
        out.append({"id": "ATel " + (m.group(1) if m else link[-12:]), "titolo": campo("title"), "link": link,
                    "data": campo("dc:date") or campo("pubDate"), "testo": campo("description")[:600],
                    "fonte": "ATel"})
    return out


def ultimo_numero_gcn(html):
    testo = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    numeri = [int(x) for x in re.findall(r"/circulars/(\d{4,6})\b", testo)]
    return max(numeri) if numeri else None


def leggi_gcn(js):
    d = json.loads(js.decode("utf-8", "replace") if isinstance(js, bytes) else js)
    data = d.get("createdOn")
    if isinstance(data, (int, float)):
        data = datetime.fromtimestamp(data / 1000, UTC).strftime("%Y-%m-%dT%H:%MZ")
    n = d.get("circularId")
    return {"id": f"GCN {n}", "titolo": d.get("subject", ""), "link": f"https://gcn.nasa.gov/circulars/{n}",
            "data": data or "", "testo": (d.get("body") or "")[:600], "fonte": "GCN"}


def per_json(x):
    """NaN e infinito non sono JSON validi: diventano None."""
    if isinstance(x, dict):
        return {k: per_json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [per_json(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


# =============================================================================
# MAPPA DEL CIELO (coordinate galattiche, proiezione di Aitoff; testi in inglese)
# =============================================================================
def galattiche(ra, dec):
    ra, dec = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    ra_p, dec_p, l_nc = np.radians(192.85948), np.radians(27.12825), np.radians(122.93192)
    sb = np.sin(dec) * np.sin(dec_p) + np.cos(dec) * np.cos(dec_p) * np.cos(ra - ra_p)
    b = np.arcsin(np.clip(sb, -1, 1))
    y = np.cos(dec) * np.sin(ra - ra_p)
    x = np.sin(dec) * np.cos(dec_p) - np.cos(dec) * np.sin(dec_p) * np.cos(ra - ra_p)
    l = (l_nc - np.arctan2(y, x)) % (2 * np.pi)
    return np.degrees(l), np.degrees(b)


COLORI_MAPPA = {"nuova": "#e63946", "esplosione": "#f4a261", "aumento": "#e9c46a", "calo": "#8ecae6",
                "calma": "#9aa5b1", "debole": "#d0d4d9", "vecchi": "#c8b6e2", "pochi": "#e0e0e0"}
NOMI_MAPPA = {"nuova": "new outburst", "esplosione": "in outburst", "aumento": "rising", "calo": "fading",
              "calma": "usual level", "debole": "too faint", "vecchi": "no recent data"}


def disegna_mappa(fig, sorgenti):
    """sorgenti: lista di dict(nome, ra, dec, stato, oggi)."""
    fig.clear()
    ax = fig.add_subplot(111, projection="aitoff")
    ax.grid(alpha=0.3)
    ordine = ["debole", "vecchi", "pochi", "calma", "calo", "aumento", "esplosione", "nuova"]
    for st in ordine:
        sel = [s for s in sorgenti if s.get("stato") == st and s.get("ra") is not None]
        if not sel:
            continue
        l, b = galattiche([s["ra"] for s in sel], [s["dec"] for s in sel])
        l = np.where(l > 180, l - 360, l)
        flusso = np.array([max(s.get("oggi") or 0, 1) for s in sel])
        taglia = 8 + 22 * np.clip(np.log10(flusso), 0, 4)
        ax.scatter(-np.radians(l), np.radians(b), s=taglia, c=COLORI_MAPPA.get(st, "#999"), edgecolors="k",
                   linewidths=0.3, label=f"{NOMI_MAPPA.get(st, st)} ({len(sel)})", zorder=ordine.index(st) + 2)
        if st in ("nuova", "esplosione"):
            for s, x, y in zip(sel, -np.radians(l), np.radians(b)):
                ax.annotate(s["nome"], (x, y), xytext=(4, 4), textcoords="offset points", fontsize="small",
                            fontweight="bold" if st == "nuova" else "normal")
    ax.set_xticklabels(["150°", "120°", "90°", "60°", "30°", "0°", "330°", "300°", "270°", "240°", "210°"])
    ax.set_title("X-ray sky seen by MAXI (galactic coordinates; size = flux)", pad=24)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.24), ncol=4, fontsize="medium", frameon=False,
              markerscale=1.6)
    fig.text(0.995, 0.005, "© 2026 Edoardo Battistel · data: MAXI (RIKEN/JAXA)", ha="right", va="bottom",
             fontsize="x-small", color="0.4")
