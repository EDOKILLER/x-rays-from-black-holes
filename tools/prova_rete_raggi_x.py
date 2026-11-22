#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAGGI X DAI BUCHI NERI - PROVA DI RETE DEGLI ARCHIVI
====================================================
© 2026 Edoardo Battistel

Prova, dalla rete di casa, tutte le fonti che il servizio userà:
  - MAXI (RIKEN)            curve di luce 2-20 keV, ogni giorno
  - Fermi GBM               occultazione terrestre, raggi X duri
  - Swift/BAT               SOLO ARCHIVIO: Swift ha chiuso il 20/10/2026
  - ATel, GCN               notizie sulle esplosioni
  - HEASARC TAP             osservazioni Chandra/XMM/NICER

Usa solo Python di base. Uso:
    python3 prova_rete_raggi_x.py

Scrive accanto a sé:
    risultati_prova_rete.txt   la tabella con gli esiti
    campioni/                  inizio e fine di ogni risposta (per vedere i formati)
Ogni richiesta ha un tempo massimo (40 s) e una seconda prova dopo 5 s.
"""
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSIONE = "1.0"
UA = "RaggiX-prova/1.0 (progetto personale di astronomia; Python urllib)"
TEMPO_MAX = 40
MAX_BYTE = 40 * 1024 * 1024
CARTELLA = Path(__file__).resolve().parent
CAMPIONI = CARTELLA / "campioni"
UTC = timezone.utc

BAT = "https://swift.gsfc.nasa.gov/results/transients"
MAXI = "https://maxi.riken.jp"
GBM = "https://batse.msfc.nasa.gov/gbm/science/earth_occ"
TAP = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"


def url_tap(adql, formato="votable"):
    p = {"REQUEST": "doQuery", "LANG": "ADQL", "QUERY": adql}
    if formato:
        p["FORMAT"] = formato
    return TAP + "?" + urllib.parse.urlencode(p)


ADQL_TABELLE = ("SELECT table_name, description FROM TAP_SCHEMA.tables WHERE "
                "table_name LIKE '%maxi%' OR table_name LIKE '%nicer%' OR table_name LIKE '%xmm%' "
                "OR table_name LIKE 'chanmaster' OR table_name LIKE '%swbat%' OR table_name LIKE '%gbm%' "
                "OR table_name LIKE '%xrb%' OR table_name LIKE '%lmxb%' OR table_name LIKE '%hmxb%' "
                "OR table_name LIKE '%einstein%' OR table_name LIKE '%ep%obs%'")
# Cyg X-1: RA 299.5903, Dec +35.2016
ADQL_NICER = ("SELECT TOP 5 * FROM nicermastr WHERE CONTAINS(POINT('ICRS',ra,dec),"
              "CIRCLE('ICRS',299.5903,35.2016,0.1))=1")
ADQL_CHANDRA = ("SELECT TOP 5 * FROM chanmaster WHERE CONTAINS(POINT('ICRS',ra,dec),"
                "CIRCLE('ICRS',299.5903,35.2016,0.1))=1")

# (gruppo, nome breve, url, tipo atteso, metodo)
PROVE = [
    # --- MAXI (la fonte principale da ora in poi) ---
    ("MAXI", "pagina principale", MAXI + "/top/index.html", "html", "GET"),
    ("MAXI", "elenco sorgenti", MAXI + "/top/slist.html", "html", "GET"),
    ("MAXI", "Cyg X-1 pagina", MAXI + "/star_data/J1958+352/J1958+352.html", "html", "GET"),
    ("MAXI", "Cyg X-1 1 giorno", MAXI + "/star_data/J1958+352/J1958+352_g_lc_1day_all.dat", "testo", "GET"),
    ("MAXI", "Cyg X-1 1 orbita", MAXI + "/star_data/J1958+352/J1958+352_g_lc_1orb_all.dat", "testo", "GET"),
    ("MAXI", "GX 339-4 1 giorno", MAXI + "/star_data/J1702-487/J1702-487_g_lc_1day_all.dat", "testo", "GET"),
    ("MAXI", "Crab 1 giorno", MAXI + "/star_data/J0534+220/J0534+220_g_lc_1day_all.dat", "testo", "GET"),
    ("MAXI", "leggimi formati", MAXI + "/top/lc_readme.txt", "testo", "GET"),
    ("MAXI", "Cyg X-1 senza https", "http://maxi.riken.jp/star_data/J1958+352/J1958+352_g_lc_1day_all.dat", "testo", "HEAD"),
    # --- Fermi GBM, occultazione terrestre (raggi X duri, sostituisce Swift/BAT) ---
    ("Fermi GBM", "elenco sorgenti", "https://batse.msfc.nasa.gov/gbm/science/earth_occ.html", "html", "GET"),
    ("Fermi GBM", "Cyg X-1 pagina", GBM + "/CYGX-1.html", "html", "GET"),
    ("Fermi GBM", "Cyg X-1 testo", GBM + "/lightcurves/CYGX-1_GBM_occ_fluxhist_auto.txt", "testo", "GET"),
    ("Fermi GBM", "Cyg X-1 FITS giorno", GBM + "/lightcurves/CYGX-1_daily_lc.fits", "fits", "GET"),
    ("Fermi GBM", "Crab testo", GBM + "/lightcurves/CRAB_GBM_occ_fluxhist_auto.txt", "testo", "GET"),
    ("Fermi GBM", "pulsar (pagina)", "https://gammaray.msfc.nasa.gov/gbm/science/pulsars.html", "html", "GET"),
    # --- Swift/BAT: solo archivio storico ---
    ("Swift/BAT", "sorgenti attuali", BAT + "/BAT_current.html", "html", "GET"),
    ("Swift/BAT", "buchi neri (pagina)", BAT + "/BlackHoles.html", "html", "GET"),
    ("Swift/BAT", "Cyg X-1 1 giorno", BAT + "/CygX-1.lc.txt", "testo", "GET"),
    ("Swift/BAT", "Cyg X-1 FITS orbita", BAT + "/CygX-1.orbit.lc.fits", "fits", "GET"),
    ("Swift/BAT", "LMC X-1 (cartella weak)", BAT + "/weak/LMCX-1.lc.txt", "testo", "GET"),
    # --- Notizie ---
    ("ATel", "RSS", "https://www.astronomerstelegram.org/?rss", "rss", "GET"),
    ("ATel", "pagina principale", "https://www.astronomerstelegram.org/", "html", "GET"),
    ("GCN", "circolari (pagina)", "https://gcn.nasa.gov/circulars", "html", "GET"),
    ("GCN", "ricerca «MAXI»", "https://gcn.nasa.gov/circulars?query=MAXI", "html", "GET"),
    ("GCN", "circolare 40000 JSON", "https://gcn.nasa.gov/circulars/40000.json", "json", "GET"),
    ("GCN", "archivio completo JSON", "https://gcn.nasa.gov/circulars/archive.json.tar.gz", "binario", "HEAD"),
    ("GCN", "notizie (stato missioni)", "https://gcn.nasa.gov/news", "html", "GET"),
    # --- HEASARC TAP ---
    ("HEASARC", "tabelle X (votable)", url_tap(ADQL_TABELLE), "votable", "GET"),
    ("HEASARC", "NICER su Cyg X-1", url_tap(ADQL_NICER), "votable", "GET"),
    ("HEASARC", "Chandra su Cyg X-1", url_tap(ADQL_CHANDRA), "votable", "GET"),
    ("HEASARC", "tabelle X senza FORMAT", url_tap(ADQL_TABELLE, None), "votable", "GET"),
]


def mjd_a_data(mjd):
    return (datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=float(mjd))).strftime("%Y-%m-%d")


def scarica(url, metodo, contesto):
    req = urllib.request.Request(url, method=metodo, headers={"User-Agent": UA, "Accept": "*/*"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TEMPO_MAX, context=contesto) as r:
        stato = r.status
        intest = dict(r.headers)
        corpo = b""
        if metodo == "GET":
            while len(corpo) < MAX_BYTE:
                pezzo = r.read(256 * 1024)
                if not pezzo:
                    break
                corpo += pezzo
                if time.time() - t0 > TEMPO_MAX * 3:
                    raise TimeoutError("scaricamento troppo lento")
    return stato, intest, corpo, time.time() - t0


def prova_una(url, metodo):
    """Due tentativi; se fallisce per il certificato, riprova senza verifica (solo per capire)."""
    ultimo = None
    for tentativo in (1, 2):
        try:
            return scarica(url, metodo, ssl.create_default_context()) + ("",)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), (e.read() or b"")[:20000], 0.0, ""
        except Exception as e:
            ultimo = e
            if isinstance(getattr(e, "reason", None), ssl.SSLError) or isinstance(e, ssl.SSLError):
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    return scarica(url, metodo, ctx) + ("certificato NON valido",)
                except Exception as e2:
                    ultimo = e2
            if tentativo == 1:
                time.sleep(5)
    raise ultimo


def righe_dati(testo):
    out = []
    for r in testo.splitlines():
        s = r.strip()
        if not s or s[0] in "#!;%/*|" or s.startswith(("COMMENT", "\\")):
            continue
        out.append(s)
    return out


def ultimo_mjd(testo):
    """Il massimo numero tra 50000 e 70000 nelle prime colonne = ultimo giorno coperto."""
    migliore = None
    for s in righe_dati(testo):
        for tok in re.split(r"[\s,]+", s)[:3]:
            try:
                v = float(tok)
            except ValueError:
                continue
            if 50000 < v < 70000:
                migliore = v if migliore is None else max(migliore, v)
                break
    return migliore


def analizza(tipo, corpo, intest):
    """Breve descrizione del contenuto."""
    if tipo == "binario" or not corpo:
        dim = intest.get("Content-Length") or intest.get("content-length")
        mod = intest.get("Last-Modified") or intest.get("last-modified") or ""
        return f"dimensione dichiarata {int(dim) / 1e6:.1f} MB {mod}".strip() if dim else (mod or "risposta vuota")
    testo = corpo.decode("utf-8", "replace")
    if tipo == "fits":
        if not corpo.startswith(b"SIMPLE"):
            return "NON è un FITS: " + testo[:80].replace("\n", " ")
        carte = [corpo[i:i + 80].decode("ascii", "replace") for i in range(0, min(len(corpo), 2880 * 6), 80)]
        utili = [c.strip() for c in carte if c[:8].strip() in ("EXTNAME", "DATE", "TSTART", "TSTOP", "MJDREFI",
                                                                 "MJDREF", "DATE-END", "OBJECT", "TELESCOP")]
        return "FITS ok; " + "; ".join(re.sub(r"\s+", " ", u)[:45] for u in utili[:8])
    if tipo == "json":
        try:
            d = json.loads(testo)
            chiavi = list(d)[:10] if isinstance(d, dict) else f"lista di {len(d)}"
            return f"JSON ok; chiavi {chiavi}; soggetto: {str(d.get('subject', ''))[:60]}"
        except Exception as e:
            return f"JSON NON leggibile ({e})"
    if tipo == "rss":
        voci = re.findall(r"<item\b.*?</item>", testo, re.S | re.I)
        titoli = [re.sub(r"<.*?>|<!\[CDATA\[|\]\]>", "", re.search(r"<title>(.*?)</title>", v, re.S).group(1)).strip()
                  for v in voci[:3] if re.search(r"<title>(.*?)</title>", v, re.S)]
        data = re.search(r"<(?:pubDate|dc:date)>(.*?)</", testo)
        return f"{len(voci)} notizie; ultima data {data.group(1) if data else '?'}; " + " | ".join(t[:50] for t in titoli)
    if tipo == "votable":
        if "<VOTABLE" not in testo[:3000].upper():
            return "NON è VOTable: " + re.sub(r"\s+", " ", testo[:120])
        err = re.search(r'<INFO[^>]*name="QUERY_STATUS"[^>]*value="ERROR"[^>]*>(.*?)</INFO>', testo, re.S)
        if err:
            return "ERRORE TAP: " + re.sub(r"\s+", " ", err.group(1))[:150]
        campi = re.findall(r'<FIELD[^>]*name="([^"]+)"', testo)
        if "<BINARY" in testo:
            return f"VOTable BINARIA ({len(campi)} colonne: {', '.join(campi[:12])}) - si leggerà con astropy"
        righe = re.findall(r"<TR>(.*?)</TR>", testo, re.S)
        prime = []
        for r in righe[:12]:
            prime.append("/".join(re.sub(r"<.*?>", "", c).strip()[:28] for c in re.findall(r"<TD>(.*?)</TD>", r, re.S)[:2]))
        return f"VOTable testo, {len(righe)} righe, {len(campi)} colonne ({', '.join(campi[:10])}); " + " ; ".join(prime)
    if tipo == "testo":
        m = ultimo_mjd(testo)
        dati = righe_dati(testo)
        info = f"{len(dati)} righe di dati"
        if m:
            giorni = (datetime.now(UTC) - (datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=m))).days
            info += f"; ultimo dato MJD {m:.2f} = {mjd_a_data(m)} ({giorni} giorni fa)"
        if dati:
            info += f"; colonne: {len(dati[-1].split())}"
        if "<html" in testo[:500].lower():
            info = "ATTENZIONE: pagina HTML invece dei dati; " + info
        return info
    # html
    titolo = re.search(r"<title>(.*?)</title>", testo, re.S | re.I)
    info = ("titolo «" + re.sub(r"\s+", " ", titolo.group(1)).strip()[:60] + "»") if titolo else "pagina senza titolo"
    agg = re.search(r"Data current through\s*([^<]{5,60})", testo)
    if agg:
        info += f"; dati aggiornati fino a: {agg.group(1).strip()}"
    stelle = len(set(re.findall(r"star_data/(J\d{4}[+-]\d{3})", testo)))
    if stelle:
        info += f"; {stelle} sorgenti MAXI"
    occ = len(set(re.findall(r"earth_occ/([A-Z0-9_+\-]+)\.html", testo)))
    if occ:
        info += f"; {occ} sorgenti GBM"
    avvisi = re.findall(r"(End of Swift[^<.]{0,60}|Swift[^<.]{0,40}(?:ended|end of)[^<.]{0,40})", testo)
    if avvisi:
        info += f"; avviso: {avvisi[0][:80]}"
    return info


def intestazioni_fits(corpo, max_hdu=4):
    """Carte di tutte le intestazioni (HDU), saltando i dati binari."""
    pos, out = 0, []
    for _ in range(max_hdu):
        if pos >= len(corpo):
            break
        carte = []
        while pos < len(corpo):
            blocco = corpo[pos:pos + 2880].decode("ascii", "replace")
            pos += 2880
            nuove = [blocco[i:i + 80].rstrip() for i in range(0, 2880, 80)]
            carte += nuove
            if any(c.strip() == "END" for c in nuove):
                break
        val = {}
        for c in carte:
            m = re.match(r"(NAXIS\d?|BITPIX|PCOUNT|GCOUNT)\s*=\s*(-?\d+)", c)
            if m:
                val[m.group(1)] = int(m.group(2))
        n = 0
        if val.get("NAXIS", 0) > 0:
            n = abs(val.get("BITPIX", 8)) // 8
            for k in range(1, val["NAXIS"] + 1):
                n *= val.get(f"NAXIS{k}", 0)
            n = (n + val.get("PCOUNT", 0)) * max(val.get("GCOUNT", 1), 1)
        pos += ((n + 2879) // 2880) * 2880
        out += [c for c in carte if c] + ["-" * 40]
    return out


def salva_campione(gruppo, nome, url, tipo, stato, intest, corpo):
    CAMPIONI.mkdir(exist_ok=True)
    f = CAMPIONI / (re.sub(r"[^A-Za-z0-9]+", "_", f"{gruppo}_{nome}").strip("_") + ".txt")
    righe = [f"URL: {url}", f"HTTP: {stato}", "INTESTAZIONI: " + json.dumps(intest, ensure_ascii=False)[:1500],
             f"BYTE: {len(corpo)}", "=" * 70]
    if tipo == "fits" and corpo.startswith(b"SIMPLE"):
        righe += intestazioni_fits(corpo)
    else:
        t = corpo.decode("utf-8", "replace").splitlines()
        righe += t[:120] + (["...", f"(saltate {len(t) - 150} righe)", "..."] + t[-30:] if len(t) > 150 else t[120:])
    f.write_text("\n".join(righe)[:200000], encoding="utf-8")


def main():
    print(f"Raggi X dai buchi neri - prova di rete {VERSIONE}   © 2026 Edoardo Battistel")
    print(f"Python {sys.version.split()[0]}   ora {datetime.now(UTC):%Y-%m-%d %H:%M} UT\n")
    risultati = []
    for gruppo, nome, url, tipo, metodo in PROVE:
        print(f"[{gruppo}] {nome} ...", end=" ", flush=True)
        try:
            stato, intest, corpo, dt, nota = prova_una(url, metodo)
            esito = "OK" if 200 <= stato < 300 else f"HTTP {stato}"
            info = analizza(tipo, corpo, intest) if esito == "OK" else re.sub(r"\s+", " ", corpo[:200].decode("utf-8", "replace"))
            if nota:
                esito += f" ({nota})"
            if esito.startswith("OK") and ("NON " in info[:12] or info.startswith(("ATTENZIONE", "ERRORE"))):
                esito = "STRANO"
            salva_campione(gruppo, nome, url, tipo, stato, intest, corpo)
            tempo = f"{dt:.1f} s"
            dim = f"{len(corpo) / 1024:.0f} kB" if corpo else "-"
        except Exception as e:
            esito, info, tempo, dim = "ERRORE", f"{type(e).__name__}: {e}"[:200], "-", "-"
        print(esito)
        risultati.append((gruppo, nome, esito, tempo, dim, info, url))

    righe = [f"RAGGI X DAI BUCHI NERI - PROVA DI RETE {VERSIONE}  (© 2026 Edoardo Battistel)",
             f"Eseguita il {datetime.now(UTC):%Y-%m-%d %H:%M} UT con Python {sys.version.split()[0]}", ""]
    for g, n, e, t, d, i, u in risultati:
        righe += [f"[{g}] {n}: {e}   ({t}, {d})", f"    {i}", f"    {u}", ""]
    ok = sum(r[2].startswith("OK") for r in risultati)
    righe.append(f"RIEPILOGO: {ok} su {len(risultati)} OK")
    for g in dict.fromkeys(r[0] for r in risultati):
        tot = [r for r in risultati if r[0] == g]
        righe.append(f"  {g}: {sum(r[2].startswith('OK') for r in tot)}/{len(tot)}")
    (CARTELLA / "risultati_prova_rete.txt").write_text("\n".join(righe) + "\n", encoding="utf-8")
    print("\n" + "\n".join(righe[-(len(set(r[0] for r in risultati)) + 1):]))
    print(f"\nRisultati scritti in {CARTELLA / 'risultati_prova_rete.txt'} e nella cartella campioni/")


if __name__ == "__main__":
    main()
