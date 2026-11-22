#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAGGI X DAI BUCHI NERI 1.1 - AVVISATORE PER IL PC (Ubuntu)
==========================================================
© 2026 Edoardo Battistel

Gira in sottofondo sul PC (parte da solo all'accesso, vedi installa.sh).
Ogni 10 minuti chiede al NAS l'elenco delle nuove esplosioni
(http://<NAS>:8097/dati/eventi.json) e mostra una notifica di Ubuntu per ognuna.
Dalla notifica, «Apri la pagina» apre la pagina dei risultati nel browser.
Avvisa anche se il servizio sul NAS non si aggiorna da più di 12 ore.
La soglia (quanto deve brillare una nuova esplosione) si cambia sul NAS, in
rx_config.json -> "soglia_avviso_mCrab".

Usa solo Python di base. Uso:
    python3 avvisatore_rx.py              avvisatore (in sottofondo)
    python3 avvisatore_rx.py --prova      manda subito una notifica di prova
    python3 avvisatore_rx.py --indirizzo  cambia l'indirizzo del NAS
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSIONE = "1.1"
APP = "Raggi X dai buchi neri"
UTC = timezone.utc
CONF = Path.home() / ".config" / "raggi_x_avvisatore.json"
ICONA = Path.home() / ".local" / "share" / "icons" / "raggi_x.png"
INDIRIZZO_PREDEFINITO = "http://192.168.1.135:8097"
INTERVALLO = 600
ORE_SILENZIO_NAS = 12


def leggi_conf():
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salva_conf(c):
    CONF.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CONF)


def chiedi_indirizzo(attuale=""):
    """Chiede l'indirizzo del NAS con zenity (finestra GNOME), o dal terminale."""
    attuale = attuale or INDIRIZZO_PREDEFINITO
    if shutil.which("zenity"):
        r = subprocess.run(["zenity", "--entry", "--title", APP, "--width", "600",
                            "--text", "Indirizzo della pagina sul NAS (con la porta, di solito 8097):",
                            "--entry-text", attuale], capture_output=True, text=True)
        v = r.stdout.strip()
    else:
        v = input(f"Indirizzo della pagina sul NAS [{attuale}]: ").strip() or attuale
    v = v.rstrip("/")
    if v and not v.startswith("http"):
        v = "http://" + v
    return v


def scarica_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"RaggiX-avvisatore/{VERSIONE}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def leggi_tempo(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except Exception:
        return None


def notifica(titolo, testo, pagina=None, urgente=True):
    """Notifica di Ubuntu; con il pulsante «Apri la pagina» se notify-send lo permette."""
    if not shutil.which("notify-send"):
        print(f"[notifica] {titolo}: {testo}", flush=True)
        return
    base = ["notify-send", "-a", APP, "-u", "critical" if urgente else "normal"]
    if ICONA.exists():
        base += ["-i", str(ICONA)]

    def con_pulsante():
        try:
            r = subprocess.run(base + ["--action=apri=Apri la pagina", "--wait", titolo, testo],
                               capture_output=True, text=True, timeout=6 * 3600)
            if r.returncode == 0:
                if r.stdout.strip() == "apri" and pagina:
                    subprocess.Popen(["xdg-open", pagina])
                return
        except Exception:
            pass
        subprocess.run(base + [titolo, testo + (f"\n{pagina}" if pagina else "")])

    threading.Thread(target=con_pulsante, daemon=True).start()


def un_controllo(conf, stato_locale):
    base = conf["indirizzo"]
    dati = scarica_json(base + "/dati/eventi.json")
    visti = set(stato_locale.setdefault("visti", []))
    adesso = datetime.now(UTC)
    primo_giro = not stato_locale.get("inizializzato")
    for e in sorted(dati.get("eventi", []), key=lambda e: e.get("rilevato_utc", "")):
        if e["id"] in visti:
            continue
        visti.add(e["id"])
        quando = leggi_tempo(e.get("rilevato_utc"))
        if primo_giro and quando and adesso - quando > timedelta(days=3):
            continue        # al primo avvio non avvisare per esplosioni vecchie
        oggi = e.get("oggi") or 0
        volte = f", {e['rapporto']:.0f} volte il solito" if e.get("rapporto") else ""
        nome = e["nome"].partition("_")[0]          # MAXI a volte attacca un commento al nome
        tipo = e.get("tipo") or "sorgente X"
        tipo = tipo[:1].upper() + tipo[1:]           # «Binaria X», non «Binaria x»
        notifica(f"✴ Nuova esplosione X: {nome}",
                 f"{tipo}. In esplosione dal {e.get('inizio', '?')}: oggi {oggi:.0f} {e.get('unita', 'mCrab')}"
                 f"{volte}.\nCurva di luce e notizie sulla pagina.", base + "/")
        print(f"{datetime.now():%Y-%m-%d %H:%M}  avviso: {e['nome']} ({e['id']})", flush=True)
    stato_locale["visti"] = sorted(visti)[-500:]
    stato_locale["inizializzato"] = True

    # il servizio sul NAS è vivo?
    try:
        st = scarica_json(base + "/dati/stato.json")
        t = leggi_tempo(st.get("aggiornato_utc"))
        fermo = t is None or adesso - t > timedelta(hours=ORE_SILENZIO_NAS)
    except Exception:
        fermo = True
    if fermo and not stato_locale.get("avvisato_fermo"):
        notifica("Raggi X: il servizio sul NAS non si aggiorna",
                 f"Nessun segnale da più di {ORE_SILENZIO_NAS} ore. Controlla il container «raggi-x» "
                 "nell'app Docker del NAS.", base + "/", urgente=False)
        stato_locale["avvisato_fermo"] = True
    elif not fermo:
        stato_locale["avvisato_fermo"] = False


def main():
    conf = leggi_conf()
    if "--indirizzo" in sys.argv or not conf.get("indirizzo"):
        ind = chiedi_indirizzo(conf.get("indirizzo", ""))
        if not ind:
            print("Nessun indirizzo: esco.")
            return
        conf["indirizzo"] = ind
        salva_conf(conf)
        # lo stesso indirizzo serve alla finestra del programma (~/.config/raggi_x.json)
        pc = Path.home() / ".config" / "raggi_x.json"
        try:
            c2 = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else {}
            c2["nas"] = ind
            pc.write_text(json.dumps(c2, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        if "--indirizzo" in sys.argv:
            return
    if "--prova" in sys.argv:
        notifica("✴ Nuova esplosione X: GX 339-4 (PROVA)",
                 "Questa è solo una prova dell'avvisatore di Raggi X dai buchi neri.", conf["indirizzo"] + "/")
        time.sleep(30)      # lascia il tempo di premere «Apri la pagina»
        return

    # una sola copia alla volta
    blocco = open(Path.home() / ".config" / "raggi_x_avvisatore.lock", "w")
    try:
        fcntl.flock(blocco, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("L'avvisatore è già in funzione.")
        return

    print(f"Avvisatore di Raggi X {VERSIONE}: controllo {conf['indirizzo']} ogni {INTERVALLO // 60} minuti",
          flush=True)
    errori = 0
    while True:
        conf = leggi_conf() or conf
        stato_locale = conf.setdefault("stato", {})
        try:
            un_controllo(conf, stato_locale)
            errori = 0
        except Exception as e:
            errori += 1
            if errori in (1, 30):   # NAS spento o PC fuori casa: non insistere
                print(f"{datetime.now():%Y-%m-%d %H:%M}  NAS non raggiungibile ({e})", flush=True)
        salva_conf(conf)
        time.sleep(INTERVALLO)


if __name__ == "__main__":
    main()
