#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAGGI X DAI BUCHI NERI 1.1 - SERVIZIO PER IL NAS (senza finestra)
=================================================================
© 2026 Edoardo Battistel

Gira di continuo dentro Docker sul NAS, a priorità minima:
  - ogni 6 ore aggiorna le curve di luce MAXI (2-20 keV) di tutte le sorgenti
    del suo elenco (~457), una richiesta al secondo, riscaricando solo i file cambiati;
  - ogni giorno le curve Fermi GBM (raggi X duri; solo IPv4, l'IPv6 verso la NASA
    di Huntsville dalla rete di casa non funziona);
  - una volta sola la storia Swift/BAT (Swift ha finito di osservare il 20/10/2026);
  - ogni 30 minuti le notizie: ATel (RSS) e circolari GCN;
  - calcola per ogni sorgente lo stato (nuova esplosione, in esplosione, in aumento,
    in calo, calma, troppo debole) e da quando dura un'esplosione;
  - mette tutto in dati/ con gli stessi nomi della cache del programma sul PC,
    più sorgenti.json, eventi.json (nuove esplosioni), notizie.json e stato.json;
  - serve la pagina web (porta 8000 del container, 8097 sul NAS) e disegna i grafici
    su richiesta: /grafico/<ID>.png?giorni=365;
  - se manca Internet aspetta e riprova la STESSA sorgente (non la salta).

Uso:  python servizio_rx.py [cartella]                 (di base /dati)
      python servizio_rx.py CARTELLA --una-volta [--max N]   un giro con la rete vera, poi esce
      python servizio_rx.py --prova                    prova senza rete (dati finti) e esce
"""
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motore_rx as M  # noqa: E402

VERSIONE = "1.1"
FIRMA = "© 2026 Edoardo Battistel"
UTC = timezone.utc
PORTA = 8000

CONFIG_PREDEFINITA = {
    "_nota": "Opzioni di Raggi X dai buchi neri. Dopo una modifica riavvia il container.",
    "ore_tra_aggiornamenti_maxi": 6,
    "ore_tra_aggiornamenti_gbm": 24,
    "minuti_tra_notizie": 30,
    "secondi_tra_richieste": 1.0,
    "soglia_avviso_mCrab": 30,
    "_soglia": "una NUOVA esplosione va nell'elenco degli avvisi (eventi.json) se brilla almeno tanto",
    "giorni_per_dire_nuova": 21,
    "swift_bat_al_giro": 40,
}

_ferma = threading.Event()
_lock_grafici = threading.Lock()


def ora_utc():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def scrivi_json(percorso, dati):
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    tmp = percorso.with_name(percorso.name + ".tmp")
    tmp.write_text(json.dumps(M.per_json(dati), ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, percorso)


def leggi_json(percorso, predefinito):
    try:
        return json.loads(Path(percorso).read_text(encoding="utf-8"))
    except Exception:
        return predefinito


class Servizio:
    def __init__(self, cartella, massimo=None):
        self.c = Path(cartella)
        self.dati = self.c / "dati"
        self.dati.mkdir(parents=True, exist_ok=True)
        (self.c / "grafici").mkdir(exist_ok=True)
        self.registro_f = self.c / "registro.txt"
        conf_f = self.c / "rx_config.json"
        self.conf = dict(CONFIG_PREDEFINITA, **leggi_json(conf_f, {}))
        scrivi_json(conf_f, self.conf)
        self.stato_f = self.c / "rx_stato.json"
        self.st = leggi_json(self.stato_f, {})
        self.fonte = M.Fonte(cache=self.dati, registro=self.log, usa_nas=False)
        self.massimo = massimo
        self.elenco = []
        salvate = leggi_json(self.dati / "sorgenti.json", {})
        self.sorgenti = {s["id"]: s for s in salvate.get("sorgenti", [])}
        self.scala = salvate.get("scala_crab")
        self.eventi = leggi_json(self.dati / "eventi.json", {}).get("eventi", [])
        self.notizie = leggi_json(self.dati / "notizie.json", {}).get("notizie", [])
        self.fase = "avvio"
        self.avanzamento = ""
        self.elenco_gbm = []

    # ------------------------------------------------------------------ utilità
    def log(self, testo):
        riga = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {testo}"
        print(riga, flush=True)
        try:
            with open(self.registro_f, "a", encoding="utf-8") as f:
                f.write(riga + "\n")
            if self.registro_f.stat().st_size > 2_000_000:
                vecchio = self.registro_f.with_suffix(".vecchio.txt")
                os.replace(self.registro_f, vecchio)
        except Exception:
            pass

    def salva_stato(self):
        scrivi_json(self.stato_f, self.st)

    def battito(self):
        scrivi_json(self.dati / "stato.json", {
            "servizio": "Raggi X dai buchi neri", "versione": VERSIONE, "aggiornato_utc": ora_utc(),
            "fase": self.fase, "avanzamento": self.avanzamento, "sorgenti": len(self.sorgenti),
            "elenco_maxi": len(self.elenco), "ultimo_giro_maxi_utc": self.st.get("ultimo_giro_maxi_utc"),
            "ultimo_giro_gbm_utc": self.st.get("ultimo_giro_gbm_utc"),
            "ultime_notizie_utc": self.st.get("ultime_notizie_utc"), "scala_crab": self.scala})

    def dorme(self, secondi):
        return _ferma.wait(secondi)

    def prendi_con_pazienza(self, chiave, url, eta_ore, tempo_max=30, cosa=""):
        """Se l'archivio non risponde aspetta (1, 2, 5, 10, 20, 30 min) e riprova la stessa cosa.
        Dopo 6 tentativi rinuncia per questo giro (la riprenderà al prossimo)."""
        for attesa in (0, 60, 120, 300, 600, 1200, 1800):
            if attesa:
                self.log(f"{cosa or chiave}: l'archivio non risponde, riprovo tra {attesa // 60} min")
                if self.dorme(attesa):
                    return None
            b = self.fonte.prendi(chiave, url, eta_ore, tempo_max=tempo_max)
            if self.fonte.ultimo_esito == "http" and attesa == 0:
                continue                      # il server risponde con un errore: un solo nuovo tentativo dopo 1 min
            if self.fonte.ultimo_esito != "rete":
                return b
        return b

    def eta_ore(self, chiave_st):
        t = self.st.get(chiave_st)
        if not t:
            return 1e9
        try:
            return (datetime.now(UTC) - datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                    ).total_seconds() / 3600
        except ValueError:
            return 1e9

    # ------------------------------------------------------------------ MAXI
    def carica_elenco(self):
        b = self.prendi_con_pazienza("maxi_elenco.html", M.MAXI + "/top/slist.html", 24, cosa="elenco MAXI")
        if b:
            self.elenco = M.leggi_elenco_maxi(b)
        c = self.prendi_con_pazienza(f"maxi/{M.ID_CRAB_MAXI}_1day.dat",
                                     f"{M.MAXI}/star_data/{M.ID_CRAB_MAXI}/{M.ID_CRAB_MAXI}_g_lc_1day_all.dat",
                                     self.conf["ore_tra_aggiornamenti_maxi"], cosa="Crab")
        if c:
            v = M.crab_maxi(M.leggi_maxi(c))
            self.scala = 1000.0 / v if v else None
        return len(self.elenco)

    def aggiorna_nomi(self):
        """Dalla 1.1 i nomi MAXI con un commento attaccato («GX 354-0_Slow-Burster…») sono separati:
        rifà subito le schede che hanno il nome vecchio, con i file già scaricati (senza rete)."""
        cambiate = 0
        for s in self.elenco:
            r = self.sorgenti.get(s["id"])
            if r and (r.get("nome") != s["nome"] or r.get("nota", None) is None):
                f = self.dati / f"maxi/{s['id']}_1day.dat"
                d = None
                if f.exists():
                    try:
                        d = M.leggi_maxi(f.read_bytes())
                    except Exception:
                        d = None
                self.sorgenti[s["id"]] = self.scheda(s, d)
                cambiate += 1
        for e in self.eventi:
            e["nome"] = e["nome"].partition("_")[0].strip()
        if cambiate:
            self.scrivi_sorgenti()
            scrivi_json(self.dati / "eventi.json", {"aggiornato_utc": ora_utc(), "eventi": self.eventi})
            self.log(f"Schede rifatte con i nomi nuovi: {cambiate}")

    def ordine_sorgenti(self):
        pref = {M.norm(n) for n, _, _ in M.PREFERITE}
        el = sorted(self.elenco, key=lambda s: (M.norm(s["nome"]) not in pref, s["nome"].lower()))
        return el[: self.massimo] if self.massimo else el

    def scheda(self, s, d):
        """Riga di sorgenti.json per una sorgente MAXI."""
        tipo = M.tipo_da_nome(s["nome"])
        r = {"id": s["id"], "nome": s["nome"], "ra": s["ra"], "dec": s["dec"], "tipo": tipo,
             "preferita": any(M.norm(n) == M.norm(s["nome"]) for n, _, _ in M.PREFERITE) or
             s["id"] in ("J1958+352", "J1702-487", "J0534+220")}
        vecchia = self.sorgenti.get(s["id"], {})
        for k in ("gbm", "gbm_2550", "gbm_ultimo", "bat", "notizie"):
            if k in vecchia:
                r[k] = vecchia[k]
        if d is None or not len(d["t"]):
            r["stato"] = "pochi"
            return r
        st = M.stato_sorgente(d, self.scala)
        if st.get("esplosione"):
            st["esplosione"]["nuova"] = bool(M.mjd_ora() - st["esplosione"]["inizio_mjd"] <=
                                             self.conf["giorni_per_dire_nuova"])
            if st["stato"] == "nuova" and not st["esplosione"]["nuova"]:
                st["stato"] = "esplosione"
        r.update({k: st.get(k) for k in ("stato", "oggi", "err_oggi", "abituale", "rapporto", "z",
                                          "durezza_oggi", "durezza_abituale", "ultimo_mjd", "unita",
                                          "giorni_senza_dati", "esplosione")})
        r["ultimo"] = M.mjd_a_data(st["ultimo_mjd"]) if st.get("ultimo_mjd") else None
        gbm = None
        if r.get("gbm"):
            g = self.fonte.cache / f"gbm/{r['gbm']}.txt"
            if g.exists():
                try:
                    gbm = M.leggi_gbm(g.read_bytes())
                except Exception:
                    gbm = None
        r["nota"] = s.get("nota", "")
        r["spiegazione"] = M.spiegazione(s["nome"], tipo, st, gbm, s.get("nota", ""))
        return r

    def giro_maxi(self):
        lista = self.ordine_sorgenti()
        self.fase = "MAXI"
        n_ok = 0
        for i, s in enumerate(lista, 1):
            if _ferma.is_set():
                return
            self.avanzamento = f"{i}/{len(lista)} {s['nome']}"
            chiave = f"maxi/{s['id']}_1day.dat"
            b = self.prendi_con_pazienza(chiave, f"{M.MAXI}/star_data/{s['id']}/{s['id']}_g_lc_1day_all.dat",
                                         self.conf["ore_tra_aggiornamenti_maxi"] - 0.5, cosa=s["nome"])
            d = None
            if b:
                try:
                    d = M.leggi_maxi(b)
                    n_ok += 1
                except Exception as e:
                    self.log(f"{s['nome']}: file MAXI non leggibile ({e})")
            self.sorgenti[s["id"]] = self.scheda(s, d)
            if self.fonte.ultimo_esito == "ok":
                self.dorme(self.conf["secondi_tra_richieste"])
            if i % 25 == 0:
                self.scrivi_sorgenti()
        self.scrivi_sorgenti()
        self.controlla_eventi()
        self.st["ultimo_giro_maxi_utc"] = ora_utc()
        self.salva_stato()
        self.log(f"Giro MAXI finito: {n_ok}/{len(lista)} sorgenti con dati; "
                 f"{sum(1 for r in self.sorgenti.values() if r.get('stato') in ('nuova', 'esplosione'))} in esplosione")

    def scrivi_sorgenti(self):
        ordine = {"nuova": 0, "esplosione": 1, "aumento": 2, "calo": 3, "calma": 4, "vecchi": 5, "debole": 6,
                  "pochi": 7}
        lista = sorted(self.sorgenti.values(), key=lambda r: (ordine.get(r.get("stato"), 9), r["nome"].lower()))
        scrivi_json(self.dati / "sorgenti.json", {"aggiornato_utc": ora_utc(), "versione": VERSIONE,
                                                   "scala_crab": self.scala, "sorgenti": lista})
        try:
            self.disegna_mappa()
        except Exception as e:
            self.log(f"Mappa non disegnata: {e}")

    def disegna_mappa(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        with _lock_grafici, matplotlib.rc_context({"font.size": 15}):
            fig = Figure(figsize=(12, 6.8), dpi=100)
            M.disegna_mappa(fig, list(self.sorgenti.values()))
            fig.subplots_adjust(left=0.03, right=0.97, top=0.88, bottom=0.17)
            tmp = self.c / "mappa.tmp.png"
            fig.savefig(tmp)
            os.replace(tmp, self.c / "mappa.png")

    def controlla_eventi(self):
        visti = {e["id"] for e in self.eventi}
        for r in self.sorgenti.values():
            es = r.get("esplosione")
            if r.get("stato") != "nuova" or not es:
                continue
            eid = f"{r['id']}_{es['inizio_mjd']:.0f}"
            if eid in visti or (r.get("oggi") or 0) < self.conf["soglia_avviso_mCrab"]:
                continue
            ev = {"id": eid, "sorgente": r["id"], "nome": r["nome"], "tipo": r["tipo"], "inizio": es["inizio"],
                  "oggi": r.get("oggi"), "picco": es["picco"], "rapporto": r.get("rapporto"),
                  "unita": r.get("unita"), "rilevato_utc": ora_utc(),
                  "pagina_maxi": f"{M.MAXI}/star_data/{r['id']}/{r['id']}.html"}
            self.eventi.append(ev)
            self.log(f"NUOVA ESPLOSIONE: {r['nome']} dal {es['inizio']}, {r.get('oggi', 0):.0f} {r.get('unita')}")
        self.eventi = self.eventi[-200:]
        scrivi_json(self.dati / "eventi.json", {"aggiornato_utc": ora_utc(), "eventi": self.eventi})

    # ------------------------------------------------------------------ Fermi GBM e Swift/BAT
    def giro_gbm(self):
        self.fase = "Fermi GBM"
        b = self.prendi_con_pazienza("gbm_elenco.html", M.GBM + "/earth_occ.html", 24, tempo_max=60,
                                     cosa="elenco Fermi GBM")
        self.elenco_gbm = M.leggi_elenco_gbm(b) if b else []
        per_nome = {M.norm(g): g for g in self.elenco_gbm}
        abbinate = 0
        for i, r in enumerate(list(self.sorgenti.values()), 1):
            if _ferma.is_set():
                return
            g = per_nome.get(M.norm("CRAB" if r["id"] == M.ID_CRAB_MAXI else r["nome"]))
            if not g:
                continue
            self.avanzamento = f"{i}/{len(self.sorgenti)} {g}"
            b = self.prendi_con_pazienza(f"gbm/{g}.txt",
                                         f"{M.GBM}/earth_occ/lightcurves/{g}_GBM_occ_fluxhist_auto.txt",
                                         self.conf["ore_tra_aggiornamenti_gbm"] - 1, tempo_max=60, cosa=g)
            if not b:
                continue
            try:
                d = M.leggi_gbm(b)
                sel = d["t"] > d["t"].max() - 7
                m, em = M.media_pesata(d["f2550"][sel], d["e2550"][sel])
                r.update(gbm=g, gbm_2550=m, gbm_ultimo=M.mjd_a_data(d["t"].max()))
                abbinate += 1
            except Exception as e:
                self.log(f"Fermi GBM {g}: non leggibile ({e})")
            self.dorme(self.conf["secondi_tra_richieste"])
        self.st["ultimo_giro_gbm_utc"] = ora_utc()
        self.salva_stato()
        self.scrivi_sorgenti()
        self.log(f"Giro Fermi GBM finito: {abbinate} sorgenti abbinate su {len(self.elenco_gbm)}")

    def giro_bat(self):
        """Storia Swift/BAT: poche sorgenti per giro, una volta sola (la missione è finita)."""
        provate = set(self.st.setdefault("bat_provate", []))
        fatte = 0
        for r in list(self.sorgenti.values()):
            if fatte >= self.conf["swift_bat_al_giro"] or _ferma.is_set():
                break
            c = M.nome_compatto("Crab" if r["id"] == M.ID_CRAB_MAXI else r["nome"])
            if c in provate:
                continue
            self.fase = "Swift/BAT (storia)"
            self.avanzamento = c
            trovato = False
            for sotto in ("", "weak/"):
                b = self.prendi_con_pazienza(f"bat/{c}.lc.txt", f"{M.BAT}/{sotto}{c}.lc.txt", 24 * 365, cosa=c)
                if b:
                    trovato = True
                    break
                if self.fonte.ultimo_esito == "rete":
                    break
            if self.fonte.ultimo_esito == "rete" and not trovato:
                break                                  # riprova al prossimo giro
            provate.add(c)
            if trovato:
                r["bat"] = c
            fatte += 1
            self.dorme(self.conf["secondi_tra_richieste"])
        self.st["bat_provate"] = sorted(provate)
        self.salva_stato()
        if fatte:
            self.scrivi_sorgenti()

    # ------------------------------------------------------------------ notizie
    def giro_notizie(self):
        self.fase = "notizie"
        nuove = []
        b = self.fonte.prendi("notizie/atel.rss", "https://www.astronomerstelegram.org/?rss", 0.4)
        if b:
            try:
                nuove += M.leggi_atel(b)
            except Exception as e:
                self.log(f"ATel non leggibile: {e}")
        h = self.fonte.prendi("notizie/gcn.html", "https://gcn.nasa.gov/circulars", 0.4)
        ultimo = M.ultimo_numero_gcn(h) if h else None
        if ultimo:
            da = max(self.st.get("gcn_ultimo", ultimo - 30) + 1, ultimo - 60)
            for n in range(da, ultimo + 1):
                j = self.fonte.prendi(f"notizie/gcn/{n}.json", f"https://gcn.nasa.gov/circulars/{n}.json", 24 * 365)
                if j:
                    try:
                        nuove.append(M.leggi_gcn(j))
                    except Exception:
                        pass
                self.st["gcn_ultimo"] = n
                if self.dorme(0.3):
                    break
        visti = {x["id"] for x in self.notizie}
        aggiunte = 0
        for x in nuove:
            if x["id"] not in visti:
                self.notizie.append(x)
                aggiunte += 1
        self.notizie = sorted(self.notizie, key=lambda x: x.get("data") or "")[-400:]
        self.abbina_notizie()
        for r in self.sorgenti.values():
            r["notizie"] = [x["id"] for x in self.notizie if r["id"] in x.get("sorgenti", [])][-10:]
        scrivi_json(self.dati / "notizie.json", {"aggiornato_utc": ora_utc(), "notizie": self.notizie})
        self.st["ultime_notizie_utc"] = ora_utc()
        self.salva_stato()
        if aggiunte:
            self.log(f"Notizie: {aggiunte} nuove (ATel e GCN)")

    def abbina_notizie(self):
        """Quali sorgenti cita ogni notizia (rifatto ogni volta: l'elenco delle sorgenti può crescere)."""
        forme = [(r["id"], M.nomi_di_ricerca(r["nome"])) for r in self.sorgenti.values()]
        for x in self.notizie:
            t = M.norm(x["titolo"] + " " + x.get("testo", "")[:300])
            x["sorgenti"] = [sid for sid, ff in forme if any(f in t for f in ff)]

    # ------------------------------------------------------------------ grafici su richiesta
    def grafico(self, sid, giorni):
        f = self.c / "grafici" / f"{sid}_{giorni}.png"
        dati_f = self.dati / f"maxi/{sid}_1day.dat"
        if not dati_f.exists():
            return None
        if f.exists() and f.stat().st_mtime > dati_f.stat().st_mtime and time.time() - f.stat().st_mtime < 6 * 3600:
            return f
        r = self.sorgenti.get(sid, {"nome": sid})
        d = M.leggi_maxi(dati_f.read_bytes())
        gbm = bat = None
        if r.get("gbm") and (self.dati / f"gbm/{r['gbm']}.txt").exists():
            gbm = M.leggi_gbm((self.dati / f"gbm/{r['gbm']}.txt").read_bytes())
        if r.get("bat") and (self.dati / f"bat/{r['bat']}.lc.txt").exists():
            bat = M.leggi_bat((self.dati / f"bat/{r['bat']}.lc.txt").read_bytes())
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        with _lock_grafici:
            matplotlib.rcParams["font.size"] = 11
            fig = Figure(figsize=(12, 7.8), dpi=100)
            M.disegna(fig, r["nome"], d, self.scala, gbm=gbm, bat=bat, giorni=giorni or None)
            tmp = f.with_name(f.name + ".tmp.png")
            fig.savefig(tmp)
            os.replace(tmp, f)
        return f

    # ------------------------------------------------------------------ ciclo
    def un_giro(self):
        if not self.elenco or self.eta_ore("elenco_utc") > 24:
            n = self.carica_elenco()
            if not n:
                self.log("Elenco MAXI non disponibile: riprovo tra 5 minuti")
                return
            self.st["elenco_utc"] = ora_utc()
            self.log(f"Elenco MAXI: {n} sorgenti; " + (f"1 Crab = {1000 / self.scala:.2f} ph/s/cm²"
                                                       if self.scala else "Crab non disponibile"))
            self.aggiorna_nomi()
        elif not self.scala:
            self.carica_elenco()
        if self.eta_ore("ultimo_giro_maxi_utc") >= self.conf["ore_tra_aggiornamenti_maxi"] or \
                len(self.sorgenti) < len(self.ordine_sorgenti()):
            self.giro_maxi()
        if self.eta_ore("ultime_notizie_utc") * 60 >= self.conf["minuti_tra_notizie"]:
            self.giro_notizie()
        if self.eta_ore("ultimo_giro_gbm_utc") >= self.conf["ore_tra_aggiornamenti_gbm"]:
            self.giro_gbm()
        self.giro_bat()
        self.fase = "in attesa"
        self.avanzamento = ""

    def avvia(self, una_volta=False):
        self.log(f"Raggi X dai buchi neri {VERSIONE} - servizio avviato in {self.c}  {FIRMA}")

        def battiti():
            while not _ferma.is_set():
                try:
                    self.battito()
                except Exception:
                    pass
                _ferma.wait(60)
        threading.Thread(target=battiti, daemon=True).start()
        while not _ferma.is_set():
            try:
                self.un_giro()
            except Exception as e:
                self.log(f"Errore nel giro: {type(e).__name__}: {e}")
                traceback.print_exc()
                self.dorme(300)
            self.battito()
            if una_volta:
                break
            self.dorme(300)
        self.salva_stato()
        self.log("Servizio fermato.")


# =============================================================================
# SERVER WEB
# =============================================================================
def crea_server(servizio, porta=PORTA):
    radice = str(servizio.c)

    class Gestore(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=radice, **k)

        def log_message(self, *a):
            pass

        def end_headers(self):
            if self.path.endswith((".json", ".html", "/")) or self.path.startswith("/grafico"):
                self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path.startswith(("/programma", "/rx_stato", "/registro")) and not u.path.startswith("/registro.txt"):
                self.send_error(404)
                return
            m = re.match(r"^/grafico/(J\d{4}[+-]\d{3})\.png$", u.path)
            if m:
                q = urllib.parse.parse_qs(u.query)
                try:
                    giorni = int(q.get("giorni", ["365"])[0])
                except ValueError:
                    giorni = 365
                giorni = giorni if giorni in (60, 365, 1826, 0) else 365
                try:
                    f = servizio.grafico(m.group(1), giorni)
                except Exception as e:
                    servizio.log(f"Grafico {m.group(1)}: {e}")
                    f = None
                if not f:
                    self.send_error(404, "Nessun dato per questa sorgente")
                    return
                corpo = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            super().do_GET()

    srv = ThreadingHTTPServer(("0.0.0.0", porta), Gestore)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def copia_pagina(cartella):
    """La pagina (index.html) è un file fisso accanto al programma: la copia nella cartella dei dati."""
    sorgente = Path(__file__).resolve().parent / "index.html"
    Path(cartella).mkdir(parents=True, exist_ok=True)      # la prima volta la cartella non c'è ancora
    if sorgente.exists():
        (Path(cartella) / "index.html").write_bytes(sorgente.read_bytes())


# =============================================================================
# PROVA SENZA RETE
# =============================================================================
def dati_finti():
    """Archivi finti: elenco MAXI, curve di luce di 8 sorgenti, Fermi GBM, Swift/BAT, ATel, GCN."""
    rng = np.random.default_rng(7)
    oggi = np.floor(M.mjd_ora()) - 0.5
    t = np.arange(oggi - 900, oggi + 0.5)
    sorg = [("Crab", "J0534+220", 83.63, 22.01, "calma", 3.5), ("Cyg X-1", "J1958+352", 299.59, 35.2, "calma", 1.0),
            ("GX 339-4", "J1702-487", 255.71, -48.79, "nuova", 0.01), ("H 1743-322", "J1746-322", 266.57, -32.23,
                                                                         "esplosione", 0.015),
            ("Aql X-1", "J1911+005", 287.82, 0.58, "debole", 0.003), ("Mrk 421", "J1104+382", 166.11, 38.21,
                                                                        "vecchi", 0.05),
            ("Sco X-1", "J1619-156", 244.98, -15.64, "calma", 45.0), ("4U 1630-47", "J1634-473", 248.5, -47.39,
                                                                       "aumento", 0.1)]
    archivio = {}
    righe_elenco = []
    for nome, sid, ra, dec, tipo, livello in sorg:
        f = np.full(t.size, livello) * (1 + 0.05 * rng.normal(size=t.size))
        e = np.full(t.size, 0.004 + 0.01 * livello)
        tt = t.copy()
        if tipo == "nuova":
            f[-9:] += np.linspace(0.05, 0.9, 9)
        elif tipo == "esplosione":
            f[-95:] += 3.0 * np.exp(-np.arange(95) / 30.0)
        elif tipo == "aumento":
            f[-3:] *= 1.8
        elif tipo == "vecchi":
            tt, f, e = tt[:-20], f[:-20], e[:-20]
        f += rng.normal(0, 1, f.size) * e
        hr = np.where(tt > oggi - 9, 0.4, 0.9) if tipo == "nuova" else np.full(tt.size, 0.7)
        f24 = f / (1 + hr + 0.3)
        testo = "\n".join(f"{a:.6f} {b:.6f} {c:.6f} {d:.6f} {c * 0.6:.6f} {d * h:.6f} {c * 0.6:.6f} {d * 0.3:.6f} "
                          f"{c:.6f}" for a, b, c, d, h in zip(tt, f, e, f24, hr))
        archivio[f"{M.MAXI}/star_data/{sid}/{sid}_g_lc_1day_all.dat"] = testo.encode()
        righe_elenco.append(f'<tr><td>{nome}</td><td align="center">{ra:.3f}, {dec:.3f}</td><td align="center">'
                            f'<a href="../star_data/{sid}/{sid}.html">&#10004;</a></td></tr>')
    archivio[M.MAXI + "/top/slist.html"] = ("<table>" + "\n".join(righe_elenco) + "</table>").encode()
    gbm_t = "\n".join(f"{a - 0.5:.6f} {a + 0.5:.6f} 600 30 800 40 900 60 800 90 20" for a in t[-400:])
    archivio[M.GBM + "/earth_occ.html"] = b'<a href="earth_occ/CYGX-1.html">Cyg X-1</a>'
    archivio[f"{M.GBM}/earth_occ/lightcurves/CYGX-1_GBM_occ_fluxhist_auto.txt"] = (
        "Name: CYGX-1\nLong-term average: 1 2 3\n" + gbm_t).encode()
    bat_t = "\n".join(f"{int(a)} 0.16 0.006 2026 1 0.002 0.005 0 1 1 1" for a in t[t < 61333])
    archivio[f"{M.BAT}/CygX-1.lc.txt"] = ("# TIME RATE\n" + bat_t).encode()
    archivio["https://www.astronomerstelegram.org/?rss"] = (
        '<rss><channel><item><title>ATel #17500: New outburst of GX 339-4 detected by MAXI</title>'
        '<link>https://www.astronomerstelegram.org/?read=17500</link><dc:date>2026-11-18T10:00:00Z</dc:date>'
        '<description>MAXI/GSC detected a brightening of GX 339-4.</description></item></channel></rss>').encode()
    archivio["https://gcn.nasa.gov/circulars"] = b'<a href="/circulars/43001">x</a><a href="/circulars/43002">y</a>'
    archivio["https://gcn.nasa.gov/circulars/43001.json"] = json.dumps(
        {"circularId": 43001, "subject": "EP260118a: Einstein Probe detection of a fast X-ray transient",
         "body": "...", "createdOn": 1795000000000}).encode()
    archivio["https://gcn.nasa.gov/circulars/43002.json"] = json.dumps(
        {"circularId": 43002, "subject": "H 1743-322: NICER observations during the decay",
         "body": "...", "createdOn": 1795100000000}).encode()
    return archivio


def prova():
    import urllib.error
    archivio = dati_finti()
    chiamate = []

    def finto_get(url, tempo_max=30, se_modificato_dopo=None, forza_ipv4=True):
        chiamate.append(url)
        if url in archivio:
            return 200, archivio[url], "Wed, 18 Nov 2026 10:00:00 GMT"
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    M.http_get = finto_get
    d = Path(tempfile.mkdtemp(prefix="raggi_x_nas_prova_"))
    copia_pagina(d)
    s = Servizio(d)
    s.conf["secondi_tra_richieste"] = 0
    s.conf["soglia_avviso_mCrab"] = 30
    srv = crea_server(s, 0)
    porta = srv.server_address[1]
    s.avvia(una_volta=True)
    ok = []

    def verifica(nome, cond):
        ok.append(bool(cond))
        print(("OK   " if cond else "FALLITO ") + nome, flush=True)

    sj = json.loads((d / "dati/sorgenti.json").read_text())
    stati = {r["nome"]: r.get("stato") for r in sj["sorgenti"]}
    print("   stati:", stati)
    verifica("8 sorgenti in sorgenti.json", len(sj["sorgenti"]) == 8)
    verifica("scala Crab", sj["scala_crab"] and abs(1000 / sj["scala_crab"] - 3.5) < 0.2)
    verifica("GX 339-4 nuova esplosione", stati.get("GX 339-4") == "nuova")
    verifica("H 1743-322 in esplosione (vecchia)", stati.get("H 1743-322") == "esplosione")
    verifica("Mrk 421 dati vecchi", stati.get("Mrk 421") == "vecchi")
    verifica("Cyg X-1 calma", stati.get("Cyg X-1") == "calma")
    ev = json.loads((d / "dati/eventi.json").read_text())["eventi"]
    verifica("evento per GX 339-4 (e solo quello)", [e["nome"] for e in ev] == ["GX 339-4"])
    cyg = next(r for r in sj["sorgenti"] if r["nome"] == "Cyg X-1")
    verifica("Fermi GBM abbinato a Cyg X-1", cyg.get("gbm") == "CYGX-1" and cyg.get("gbm_2550"))
    verifica("Swift/BAT trovato per Cyg X-1", cyg.get("bat") == "CygX-1")
    no = json.loads((d / "dati/notizie.json").read_text())["notizie"]
    gx = next(r for r in sj["sorgenti"] if r["nome"] == "GX 339-4")
    verifica(f"notizie ATel+GCN ({len(no)}) e abbinamento", len(no) == 3 and gx.get("notizie") == ["ATel 17500"])
    verifica("GCN in ordine (ultimo 43002)", s.st.get("gcn_ultimo") == 43002)
    verifica("solo IPv4 non richiesto qui (finto)", True)
    import urllib.request
    base = f"http://127.0.0.1:{porta}"
    pag = urllib.request.urlopen(base + "/", timeout=10).read()
    verifica("pagina servita", b"Raggi X dai buchi neri" in pag)
    st = json.loads(urllib.request.urlopen(base + "/dati/stato.json", timeout=10).read())
    verifica("stato.json (il PC lo usa per sapere se il NAS c'è)", st["versione"] == VERSIONE)
    png = urllib.request.urlopen(base + "/grafico/J1958+352.png?giorni=365", timeout=60).read()
    verifica(f"grafico su richiesta ({len(png) // 1024} kB)", png[:4] == b"\x89PNG")
    verifica("mappa del cielo", (d / "mappa.png").exists())
    maxi = urllib.request.urlopen(base + "/dati/maxi/J1958+352_1day.dat", timeout=10).read()
    verifica("file MAXI per il PC con lo stesso nome della cache", len(M.leggi_maxi(maxi)["t"]) > 800)
    try:
        urllib.request.urlopen(base + "/rx_stato.json", timeout=10)
        verifica("file interni non serviti", False)
    except urllib.error.HTTPError:
        verifica("file interni non serviti", True)
    # secondo giro: niente doppioni negli eventi, nessun riscaricamento inutile
    n = len(chiamate)
    s.un_giro()
    verifica("secondo giro senza riscaricare MAXI", sum("star_data" in c for c in chiamate[n:]) == 0)
    verifica("nessun evento doppio", len(json.loads((d / "dati/eventi.json").read_text())["eventi"]) == 1)
    srv.shutdown()
    print(f"\n{sum(ok)}/{len(ok)} " + ("PROVA SERVIZIO SUPERATA" if all(ok) else "CI SONO ERRORI") + f"   ({d})")
    return all(ok)


def main():
    arg = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--prova" in sys.argv:
        sys.exit(0 if prova() else 1)
    cartella = Path(arg[0] if arg else os.environ.get("RX_DATI", "/dati"))
    massimo = None
    if "--max" in sys.argv:
        massimo = int(sys.argv[sys.argv.index("--max") + 1])
    copia_pagina(cartella)
    s = Servizio(cartella, massimo)
    signal.signal(signal.SIGTERM, lambda *a: _ferma.set())
    signal.signal(signal.SIGINT, lambda *a: _ferma.set())
    if "--una-volta" in sys.argv:
        s.avvia(una_volta=True)
        print(f"\nFatto. Apri {cartella / 'index.html'} oppure, per vedere anche i grafici:\n"
              f"  python3 {Path(__file__).name} {cartella} --solo-pagina   (poi http://localhost:8097)")
        return
    if "--solo-pagina" in sys.argv:
        crea_server(s, 8097)
        print("Pagina su http://localhost:8097  (Ctrl+C per uscire)")
        while not _ferma.wait(1):
            pass
        return
    crea_server(s, PORTA)
    s.log(f"Pagina web attiva sulla porta {PORTA} del container (8097 sul NAS)")
    s.avvia()


if __name__ == "__main__":
    main()
