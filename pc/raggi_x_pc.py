#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAGGI X DAI BUCHI NERI 1.1 - PROGRAMMA PER IL PC
================================================
© 2026 Edoardo Battistel

Finestra che mostra il cielo X variabile: binarie X con buchi neri e stelle di neutroni,
pulsar X, nuclei galattici. Per ogni sorgente: curva di luce MAXI (2-20 keV), Fermi GBM
(25-50 keV), la storia di Swift/BAT (finita il 20/10/2026), il rapporto di durezza e il
diagramma durezza-intensità. Dice se una sorgente «sta esplodendo».

I dati li chiede PRIMA al servizio sul NAS (http://192.168.1.135:8097): da lì arriva subito
lo stato di tutte le sorgenti. Se il NAS non risponde (fuori casa, servizio fermo) va
direttamente agli archivi e calcola da solo. Cache in ~/.cache/raggi_x.

Uso:
    python3 raggi_x_pc.py                 finestra
    python3 raggi_x_pc.py --autotest      prova senza rete (apre e chiude una finestra)
    python3 raggi_x_pc.py --diagnostica   misura i tempi di rete (Fermi GBM con e senza IPv4)
"""
import queue
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motore_rx as M  # noqa: E402

TITOLO = f"Raggi X dai buchi neri {M.VERSIONE}"
CARTELLA_IMMAGINI = Path.home() / "Scrivania" / "Astronomia Analizzati" / "Raggi_X"
DIMENSIONI = ["100 %", "125 %", "150 %", "175 %", "200 %"]
COLORI_STATO = {"nuova": "#ff8a8a", "esplosione": "#ffc4c4", "aumento": "#ffe0a3", "calo": "#d6e8ff",
                "debole": "#e6e6e6", "vecchi": "#f0e6ff", "pochi": "#eeeeee"}
ID_NOTI = {"Cyg X-1": "J1958+352", "GX 339-4": "J1702-487", "Crab": "J0534+220"}


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def trova_id(nome, elenco):
    if nome in ID_NOTI:
        return ID_NOTI[nome], nome
    n = norm(nome)
    for s in elenco:
        if norm(s["nome"]) == n:
            return s["id"], s["nome"]
    for s in elenco:
        if n and (n in norm(s["nome"]) or norm(s["nome"]) in n) and len(norm(s["nome"])) > 4:
            return s["id"], s["nome"]
    return None, None


def nome_gbm(nome, elenco_gbm):
    n = norm("CRAB" if nome == "Crab" else nome)
    for g in elenco_gbm:
        if norm(g) == n:
            return g
    return None


# =============================================================================
# DATI (in un filo separato: la finestra non si blocca)
# =============================================================================
class Dati:
    def __init__(self, registro):
        self.fonte = M.Fonte(registro=registro)
        self.registro = registro
        self.elenco = []
        self.elenco_gbm = None
        self.scala = None              # ph/s/cm2 della Crab = 1000 mCrab
        self.maxi = {}
        self.gbm = {}
        self.bat = {}

    def carica_elenco(self, forza=False):
        b = self.fonte.prendi("maxi_elenco.html", M.MAXI + "/top/slist.html", 0 if forza else 24)
        self.elenco = M.leggi_elenco_maxi(b) if b else []
        c = self.fonte.prendi(f"maxi/{M.ID_CRAB_MAXI}_1day.dat",
                              f"{M.MAXI}/star_data/{M.ID_CRAB_MAXI}/{M.ID_CRAB_MAXI}_g_lc_1day_all.dat", 24)
        if c:
            self.scala = 1000.0 / M.crab_maxi(M.leggi_maxi(c)) if M.crab_maxi(M.leggi_maxi(c)) else None
        return len(self.elenco)

    def dal_nas(self):
        """Stato di tutte le sorgenti già calcolato dal servizio sul NAS (None se il NAS non c'è)."""
        if not self.fonte._prova_nas():
            return None
        try:
            cod, b, _ = M.http_get(self.fonte.nas + "/dati/sorgenti.json", tempo_max=20)
            d = __import__("json").loads(b)
            if d.get("scala_crab"):
                self.scala = d["scala_crab"]
            return d
        except Exception as e:
            self.registro(f"NAS: sorgenti.json non letto ({e}); calcolo da solo")
            return None

    def carica_elenco_gbm(self):
        t0 = time.time()
        b = self.fonte.prendi("gbm_elenco.html", M.GBM + "/earth_occ.html", 72, tempo_max=90)
        self.elenco_gbm = M.leggi_elenco_gbm(b) if b else []
        return time.time() - t0

    def maxi_di(self, mid, forza=False):
        b = self.fonte.prendi(f"maxi/{mid}_1day.dat", f"{M.MAXI}/star_data/{mid}/{mid}_g_lc_1day_all.dat",
                              0 if forza else 3)
        if not b:
            return None
        d = M.leggi_maxi(b)
        self.maxi[mid] = d
        return d

    def gbm_di(self, nome):
        if self.elenco_gbm is None:
            self.carica_elenco_gbm()
        g = nome_gbm(nome, self.elenco_gbm or [])
        if not g:
            return None
        if g not in self.gbm:
            b = self.fonte.prendi(f"gbm/{g}.txt", f"{M.GBM}/earth_occ/lightcurves/{g}_GBM_occ_fluxhist_auto.txt",
                                  12, tempo_max=90)
            self.gbm[g] = M.leggi_gbm(b) if b else None
        return self.gbm[g]

    def bat_di(self, nome):
        c = M.nome_compatto(nome)
        if c in self.bat:
            return self.bat[c]
        d = None
        for sotto in ("", "weak/"):
            b = self.fonte.prendi(f"bat/{c}.lc.txt", f"{M.BAT}/{sotto}{c}.lc.txt", 24 * 30, tentativi=1)
            if b:
                d = M.leggi_bat(b)
                break
        self.bat[c] = d
        return d


# =============================================================================
# FINESTRA
# =============================================================================
def avvia_finestra(autotest=False):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

    class Fila(ttk.Frame):
        """Fila di comandi che va a capo quando la finestra è stretta (come le parole di un testo)."""

        def __init__(self, master, spazio=6, margine=6):
            super().__init__(master)
            self.el, self.spazio, self.margine, self._alto = [], spazio, margine, 0
            self.bind("<Configure>", lambda e: self.disponi())

        def aggiungi(self, w):
            self.el.append(w)
            return w

        def gruppo(self):
            """Un riquadro per tenere insieme etichetta e tendina (vanno a capo insieme)."""
            return self.aggiungi(ttk.Frame(self))

        def disponi(self):
            try:
                larg = int(self.winfo_width())
                if larg <= 1:
                    larg = int(self.winfo_toplevel().winfo_width())
                m, sp = self.margine, self.spazio
                x = y = m
                alto_riga = 0
                for w in self.el:
                    rw, rh = int(w.winfo_reqwidth()), int(w.winfo_reqheight())
                    if x > m and x + rw > larg - m:
                        x, y = m, y + alto_riga + sp
                        alto_riga = 0
                    w.place(x=x, y=y)
                    x += rw + sp
                    alto_riga = max(alto_riga, rh)
                alto = y + alto_riga + m
                if alto != self._alto:
                    self._alto = alto
                    self.configure(height=alto)
            except Exception:
                pass

    class App(tk.Tk):
        def __init__(self):
            super().__init__(className="RaggiX")      # classe finestra: «Raggix»
            self.title(TITOLO)
            self.stile = ttk.Style(self)
            try:
                self.stile.theme_use("clam")          # il tema di serie non lascia ingrandire frecce e quadratini
            except tk.TclError:
                pass
            self.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
            try:                                           # icona della finestra (dopo installa.sh)
                f_icona = Path.home() / ".local" / "share" / "icons" / "raggi_x.png"
                if f_icona.exists():
                    self._icona = tk.PhotoImage(file=str(f_icona))
                    self.iconphoto(True, self._icona)
            except Exception:
                pass
            self.conf = M.leggi_conf()
            self.coda = queue.Queue()
            self.dati = Dati(lambda s: self.coda.put(("log", s)))
            self.righe = {}            # nome -> dict(id, tipo, stato)
            self.scelta = None
            self.occupato = 0
            self._costruisci()
            self.applica_dimensione()
            self.geometry(self.conf.get("geometria", "1500x900"))
            self.protocol("WM_DELETE_WINDOW", self.chiudi)
            self.after(150, self._leggi_coda)
            if not autotest:
                self.lavora(self._avvio)

        # ---------------------------------------------------------------- interfaccia
        def _costruisci(self):
            barra = Fila(self)
            barra.pack(fill="x")
            g = barra.gruppo()
            ttk.Label(g, text="Mostra:").pack(side="left")
            self.v_filtro = tk.StringVar(value="Preferite")
            cb = ttk.Combobox(g, textvariable=self.v_filtro, state="readonly", width=22,
                              values=["Preferite", "Tutte le sorgenti MAXI"])
            cb.pack(side="left", padx=(4, 8))
            cb.bind("<<ComboboxSelected>>", lambda e: self.riempi_lista())
            g = barra.gruppo()
            ttk.Label(g, text="Cerca:").pack(side="left")
            self.v_cerca = tk.StringVar()
            en = ttk.Entry(g, textvariable=self.v_cerca, width=14)
            en.pack(side="left", padx=(4, 8))
            en.bind("<KeyRelease>", lambda e: self.riempi_lista())
            g = barra.gruppo()
            ttk.Label(g, text="Periodo:").pack(side="left")
            self.v_periodo = tk.StringVar(value=self.conf.get("periodo", "1 anno"))
            cp = ttk.Combobox(g, textvariable=self.v_periodo, state="readonly", width=9,
                              values=list(M.PERIODI))
            cp.pack(side="left", padx=(4, 8))
            cp.bind("<<ComboboxSelected>>", lambda e: self.ridisegna())
            self.v_bat = tk.BooleanVar(value=self.conf.get("mostra_bat", True))
            barra.aggiungi(ttk.Checkbutton(barra, text="Storia Swift/BAT", variable=self.v_bat,
                                           command=self.mostra_scelta))
            g = barra.gruppo()
            ttk.Label(g, text="Dimensione testo:").pack(side="left")
            self.v_dim = tk.StringVar(value=self.conf.get("dimensione", "150 %"))
            cd = ttk.Combobox(g, textvariable=self.v_dim, state="readonly", width=6, values=DIMENSIONI)
            cd.pack(side="left", padx=4)
            cd.bind("<<ComboboxSelected>>", lambda e: (self.applica_dimensione(), self.ridisegna()))

            barra2 = Fila(self)
            barra2.pack(fill="x")
            for testo, cmd in (("Aggiorna", self.aggiorna), ("Salva immagine", self.salva_immagine),
                               ("Pagina MAXI", self.apri_maxi), ("Circolari GCN", self.apri_gcn),
                               ("Pagina del NAS", self.apri_nas), ("Diagnostica rete", self.diagnostica)):
                barra2.aggiungi(ttk.Button(barra2, text=testo, command=cmd))
            self.file_comandi = [barra, barra2]

            pw = self.pw = ttk.PanedWindow(self, orient="horizontal")
            pw.pack(fill="both", expand=True, padx=6)
            sx = ttk.Frame(pw)
            colonne = ("stato", "oggi", "abituale", "volte")
            self.albero = ttk.Treeview(sx, columns=colonne, selectmode="browse")
            self.albero.heading("#0", text="Sorgente")
            for c, t in zip(colonne, ("Stato", "Oggi", "Di solito", "Volte")):
                self.albero.heading(c, text=t)
            sb = ttk.Scrollbar(sx, orient="vertical", command=self.albero.yview)
            self.albero.configure(yscrollcommand=sb.set)
            self.albero.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.albero.bind("<<TreeviewSelect>>", lambda e: self.mostra_scelta())
            for s, col in COLORI_STATO.items():
                self.albero.tag_configure(s, background=col)
            pw.add(sx, weight=1)

            dx = ttk.PanedWindow(pw, orient="vertical")
            fr = ttk.Frame(dx)
            self.fig = Figure(figsize=(10, 6.5), dpi=100)
            self.canvas = FigureCanvasTkAgg(self.fig, master=fr)
            # barra di matplotlib nascosta (icone minuscole): al suo posto pulsanti grandi in italiano
            self.barra_mpl = NavigationToolbar2Tk(self.canvas, fr, pack_toolbar=False)
            self.barra_mpl.update()
            comandi = Fila(fr, spazio=4, margine=2)
            comandi.pack(side="bottom", fill="x", pady=(2, 0))
            for testo, cmd in (("⌂ Vista iniziale", self.barra_mpl.home), ("◀ Indietro", self.barra_mpl.back),
                               ("Avanti ▶", self.barra_mpl.forward), ("✥ Sposta", self.barra_mpl.pan),
                               ("⊕ Ingrandisci", self.barra_mpl.zoom)):
                comandi.aggiungi(ttk.Button(comandi, text=testo, command=cmd))
            self.v_coord = tk.StringVar(value="")
            comandi.aggiungi(ttk.Label(comandi, textvariable=self.v_coord, foreground="#555"))
            self.file_comandi.append(comandi)
            self.canvas.mpl_connect("motion_notify_event", self._coordinate)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            dx.add(fr, weight=4)
            ft = ttk.Frame(dx)
            self.testo = tk.Text(ft, wrap="word", height=7, relief="flat", padx=8, pady=6)
            st = ttk.Scrollbar(ft, orient="vertical", command=self.testo.yview)
            self.testo.configure(yscrollcommand=st.set)
            self.testo.pack(side="left", fill="both", expand=True)
            st.pack(side="left", fill="y")
            dx.add(ft, weight=1)
            pw.add(dx, weight=3)

            basso = ttk.Frame(self, padding=(6, 3))
            basso.pack(fill="x")
            self.v_stato = tk.StringVar(value="Avvio…")
            ttk.Label(basso, textvariable=self.v_stato, anchor="w").pack(side="left", fill="x", expand=True)
            ttk.Label(basso, text="© 2026 Edoardo Battistel", foreground="#666").pack(side="right")
            self.scrivi("Benvenuto. A sinistra le sorgenti; scegline una per vedere i grafici.\n"
                        "Colori: rosso = nuova esplosione, rosa = in esplosione, arancio = in aumento, "
                        "azzurro = in calo, grigio = troppo debole, lilla = dati vecchi.")

        def applica_dimensione(self):
            pct = int(self.v_dim.get().split()[0])
            base = round(10 * pct / 100)
            for n in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkFixedFont"):
                try:
                    tkfont.nametofont(n).configure(size=base)
                except tk.TclError:
                    pass
            alto = tkfont.nametofont("TkDefaultFont").metrics("linespace")
            stile = ttk.Style(self)
            stile.configure("Treeview", rowheight=alto + 6)
            larg = tkfont.nametofont("TkDefaultFont").measure("0")
            self.albero.column("#0", width=larg * 20, stretch=True)
            larghezze = {"#0": 19, "stato": 15, "oggi": 8, "abituale": 9, "volte": 7}
            self.albero.column("#0", width=larg * larghezze["#0"], stretch=True)
            for c in ("stato", "oggi", "abituale", "volte"):
                self.albero.column(c, width=larg * larghezze[c], anchor="center" if c == "stato" else "e",
                                   stretch=False)
            self.testo.configure(font=tkfont.nametofont("TkTextFont"))
            # frecce, quadratini, barre di scorrimento e pulsanti in proporzione al testo
            s = self.stile
            s.configure("TScrollbar", arrowsize=int(alto * 0.95))
            s.configure("TCombobox", arrowsize=int(alto * 0.9), padding=int(alto * 0.15))
            s.configure("TCheckbutton", indicatorsize=int(alto * 0.75), indicatormargin=int(alto * 0.2))
            s.configure("TButton", padding=(int(alto * 0.4), int(alto * 0.2)))
            s.configure("TEntry", padding=int(alto * 0.15))
            s.configure("Sash", sashthickness=max(8, int(alto * 0.45)), gripcount=8)
            s.configure("Treeview.Heading", padding=int(alto * 0.15))
            # grafico: punti per pollice proporzionali (le scritte crescono con tutto il resto)
            matplotlib.rcParams["font.size"] = 9
            self.dpi_grafico = 100 * pct / 100
            try:
                self.fig._original_dpi = self.dpi_grafico
                self.fig.set_dpi(self.dpi_grafico)
                w = self.canvas.get_tk_widget().winfo_width()
                h = self.canvas.get_tk_widget().winfo_height()
                if w > 20 and h > 20:
                    self.fig.set_size_inches(w / self.dpi_grafico, h / self.dpi_grafico, forward=False)
            except Exception:
                pass
            # le file di comandi si ridispongono con la nuova misura del testo
            self.after(80, lambda: [f.disponi() for f in getattr(self, "file_comandi", [])])
            # lista larga quanto le sue colonne
            self.after(300, lambda: self._larghezza_lista(larg * sum(larghezze.values()) + int(alto * 1.6)))

        def _larghezza_lista(self, px):
            try:
                self.pw.sashpos(0, int(px))
            except Exception:
                pass

        def _coordinate(self, ev):
            if ev.inaxes is not None and ev.xdata is not None:
                ax = ev.inaxes
                if ax.get_xlabel().startswith("Hardness"):
                    self.v_coord.set(f"durezza {ev.xdata:.2f}   flusso {ev.ydata:.0f}")
                else:
                    try:
                        import matplotlib.dates as mdates
                        data = mdates.num2date(ev.xdata).strftime("%d/%m/%Y")
                    except Exception:
                        data = f"{ev.xdata:.1f}"
                    self.v_coord.set(f"{data}   {ev.ydata:.2f}")
            else:
                self.v_coord.set("")

        def scrivi(self, testo):
            self.testo.configure(state="normal")
            self.testo.delete("1.0", "end")
            self.testo.insert("1.0", testo)
            self.testo.configure(state="disabled")

        # ---------------------------------------------------------------- lavoro in sottofondo
        def lavora(self, funzione, *arg):
            self.occupato += 1

            def corri():
                try:
                    funzione(*arg)
                except Exception as e:
                    self.coda.put(("log", f"Errore: {type(e).__name__}: {e}"))
                    traceback.print_exc()
                finally:
                    self.coda.put(("fine", None))
            threading.Thread(target=corri, daemon=True).start()

        def _leggi_coda(self):
            try:
                while True:
                    tipo, val = self.coda.get_nowait()
                    if tipo == "log":
                        self.v_stato.set(val)
                        print(val, flush=True)
                    elif tipo == "fine":
                        self.occupato -= 1
                    elif tipo == "lista":
                        self.riempi_lista()
                    elif tipo == "riga":
                        self.aggiorna_riga(val)
                    elif tipo == "grafico":
                        self._disegna(*val)
                    elif tipo == "testo":
                        self.scrivi(val)
            except queue.Empty:
                pass
            self.after(150, self._leggi_coda)

        def _avvio(self, forza=False):
            self.coda.put(("log", "Chiedo i dati al NAS…"))
            nas = self.dati.dal_nas()
            if nas and nas.get("sorgenti"):
                self._avvio_dal_nas(nas)
                return
            self.coda.put(("log", "Il NAS non risponde: leggo l'elenco delle sorgenti MAXI dagli archivi…"))
            n = self.dati.carica_elenco(forza)
            nuove = {}
            for nome, comune, tipo in M.PREFERITE:
                mid, nome_maxi = trova_id(nome, self.dati.elenco)
                nuove[comune] = {"id": mid, "tipo": tipo, "nome_maxi": nome_maxi or nome, "preferita": True}
            usati = {r["id"] for r in nuove.values() if r["id"]}
            for s in self.dati.elenco:
                if s["id"] not in usati:
                    usati.add(s["id"])
                    nuove.setdefault(s["nome"], {"id": s["id"], "tipo": "", "nome_maxi": s["nome"],
                                                 "preferita": False})
            self.righe = nuove             # sostituzione in un colpo solo (la finestra legge in parallelo)
            self.coda.put(("lista", None))
            scala = f"1 Crab = {1000 / self.dati.scala:.2f} ph/s/cm² in MAXI" if self.dati.scala else "senza la Crab: unità ph/s/cm²"
            self.coda.put(("log", f"{n} sorgenti MAXI; {scala}. Scarico le preferite…"))
            pref = [c for c, r in self.righe.items() if r.get("preferita")]
            for i, nome in enumerate(pref, 1):
                self.calcola(nome, forza)
                self.coda.put(("log", f"Preferite: {i}/{len(pref)} ({nome})"))
            da = sorted(set(self.dati.fonte.provenienza.values()))
            self.coda.put(("log", f"Pronto. Dati da: {', '.join(da)}. Fermi GBM: leggo l'elenco in sottofondo…"))
            dt = self.dati.carica_elenco_gbm()
            self.coda.put(("log", f"Pronto. Fermi GBM: {len(self.dati.elenco_gbm or [])} sorgenti, elenco in {dt:.0f} s."))

        def _avvio_dal_nas(self, nas):
            """Tutte le sorgenti con lo stato calcolato sul NAS: niente da scaricare all'avvio."""
            comuni = {}
            for nome, comune, tipo in M.PREFERITE:
                comuni[M.norm(nome)] = (comune, tipo)
            nuove = {}
            for r in nas["sorgenti"]:
                comune, tipo = comuni.get(M.norm(r["nome"]), (r["nome"], r.get("tipo") or ""))
                if r["id"] == M.ID_CRAB_MAXI:
                    comune = "Nebulosa del Granchio"
                nuove[comune] = {"id": r["id"], "tipo": tipo or r.get("tipo", ""), "nome_maxi": r["nome"],
                                 "preferita": bool(r.get("preferita")), "stato": r, "dal_nas": True}
            self.righe = nuove
            self.coda.put(("lista", None))
            quando = (nas.get("aggiornato_utc") or "").replace("T", " ").replace("Z", " UT")
            accese = sum(1 for r in nas["sorgenti"] if r.get("stato") in ("nuova", "esplosione"))
            self.coda.put(("log", f"Dati dal NAS: {len(nuove)} sorgenti, {accese} in esplosione "
                                  f"(calcolati il {quando})."))

        def calcola(self, nome, forza=False):
            r = self.righe.get(nome)
            if not r or not r.get("id"):
                if r is not None:
                    r["stato"] = {"stato": "pochi"}
                    self.coda.put(("riga", nome))
                return None
            d = self.dati.maxi_di(r["id"], forza)
            if r.get("dal_nas") and r.get("stato"):
                return d                                # lo stato l'ha già calcolato il NAS
            r["stato"] = M.stato_sorgente(d, self.dati.scala) if d is not None and len(d["t"]) else {"stato": "pochi"}
            self.coda.put(("riga", nome))
            return d

        # ---------------------------------------------------------------- lista
        def _valori(self, nome):
            st = self.righe[nome].get("stato")
            if not st:
                return ("…", "", "", "")
            if st.get("oggi") is None or st.get("abituale") is None:
                return (M.STATI.get(st.get("stato"), ""), "", "", "")
            rap = st.get("rapporto")
            if rap is None:
                rap = float("inf") if st.get("stato") in ("nuova", "esplosione") else float("nan")
            fm = "{:.0f}" if st.get("unita") == "mCrab" else "{:.3f}"

            def val(x):
                return "≈0" if x <= 0 or fm.format(x) in ("0", "0.000") else fm.format(x)
            return (M.STATI.get(st["stato"], ""), val(st["oggi"]), val(st["abituale"]),
                    f"{rap:.1f}" if np.isfinite(rap) else ("∞" if rap == float("inf") else ""))

        def riempi_lista(self):
            self.albero.delete(*self.albero.get_children())
            tutte = self.v_filtro.get().startswith("Tutte")
            cerca = norm(self.v_cerca.get())
            nomi = [n for n, r in self.righe.items() if (tutte or r.get("preferita"))]
            if tutte:
                nomi.sort(key=lambda n: (not self.righe[n].get("preferita"), n.lower()))
            for n in nomi:
                if cerca and cerca not in norm(n) and cerca not in norm(self.righe[n].get("id") or ""):
                    continue
                st = (self.righe[n].get("stato") or {}).get("stato", "")
                self.albero.insert("", "end", iid=n, text=n, values=self._valori(n), tags=(st,))
            if self.scelta and self.albero.exists(self.scelta):
                self.albero.selection_set(self.scelta)
                self.albero.see(self.scelta)

        def aggiorna_riga(self, nome):
            if self.albero.exists(nome):
                st = (self.righe[nome].get("stato") or {}).get("stato", "")
                self.albero.item(nome, values=self._valori(nome), tags=(st,))
            if nome == self.scelta:
                self.mostra_scelta()

        # ---------------------------------------------------------------- grafici
        def mostra_scelta(self):
            sel = self.albero.selection()
            if not sel:
                return
            nome = sel[0]
            cambiata = nome != self.scelta
            self.scelta = nome
            r = self.righe[nome]
            if cambiata:
                self.v_stato.set(f"Carico {nome}…")
            self.lavora(self._prepara_grafico, nome, self.v_bat.get())

        def _prepara_grafico(self, nome, con_bat):
            r = self.righe[nome]
            d = self.dati.maxi.get(r.get("id")) if r.get("id") else None
            if d is None:
                d = self.calcola(nome)
            if d is None or not len(d["t"]):
                self.coda.put(("testo", f"{nome}: MAXI non ha dati per questa sorgente."))
                return
            nm = r.get("nome_maxi") or nome
            self.coda.put(("grafico", (nome, d, None, None)))       # subito MAXI
            self.coda.put(("log", f"{nome}: cerco Fermi GBM" + (" e Swift/BAT…" if con_bat else "…")))
            st_nas = r.get("stato") if r.get("dal_nas") else None
            # se il NAS ha già cercato e non ha trovato, inutile cercare negli archivi
            g = self.dati.gbm_di(nm) if not st_nas or st_nas.get("gbm") else None
            b = self.dati.bat_di(st_nas.get("bat") if st_nas and st_nas.get("bat") else nm) \
                if con_bat and (not st_nas or st_nas.get("bat")) else None
            self.coda.put(("grafico", (nome, d, g, b)))
            altri = ("Fermi GBM sì" if g is not None else "Fermi GBM no") + \
                    ((", Swift/BAT sì" if b is not None else ", Swift/BAT no") if con_bat else "")
            self.coda.put(("log", f"{nome}: MAXI fino al {M.mjd_a_data(d['t'].max())}; {altri}. "
                                  f"Dati da: {self.dati.fonte.provenienza.get('maxi/' + r['id'] + '_1day.dat', '?')}"))

        def _disegna(self, nome, d, g, b):
            if nome != self.scelta:
                return
            self._ultimo = (nome, d, g, b)
            r = self.righe[nome]
            try:
                M.disegna(self.fig, r.get("nome_maxi") or nome, d, self.dati.scala, gbm=g, bat=b,
                          giorni=M.PERIODI[self.v_periodo.get()])
            except Exception as e:
                self.fig.clear()
                self.fig.text(0.5, 0.5, f"Plot error: {e}", ha="center")
            self.canvas.draw_idle()
            st = r.get("stato") or M.stato_sorgente(d, self.dati.scala)
            testo = st.get("spiegazione") if r.get("dal_nas") and st.get("spiegazione") else \
                M.spiegazione(r.get("nome_maxi") or nome, r.get("tipo") or "sorgente X", st, g)
            self.scrivi(testo +
                        "\n\nCome si legge: in alto la luminosità nei raggi X giorno per giorno; in basso a sinistra "
                        "la «durezza» (quanto lo spettro è spostato verso i raggi X più energetici); in basso a "
                        "destra il diagramma durezza-intensità: un buco nero in esplosione di solito parte DURO "
                        "(a destra), sale, poi diventa MORBIDO (a sinistra) e alla fine torna duro mentre si spegne: "
                        "disegna una «q» rovesciata. La stella rossa è l'ultimo giorno.")

        def ridisegna(self):
            self.conf["periodo"] = self.v_periodo.get()
            if getattr(self, "_ultimo", None):
                self._disegna(*self._ultimo)

        # ---------------------------------------------------------------- pulsanti
        def aggiorna(self):
            for r in self.righe.values():
                r.pop("stato", None)
            self.dati.gbm.clear()
            self.dati.maxi.clear()
            self.dati.fonte.nas_attivo = None          # ricontrolla se il NAS c'è
            self.riempi_lista()
            self.lavora(self._avvio, True)

        def salva_immagine(self):
            if not getattr(self, "_ultimo", None):
                self.v_stato.set("Scegli prima una sorgente.")
                return
            CARTELLA_IMMAGINI.mkdir(parents=True, exist_ok=True)
            nome = "".join(c if c.isalnum() or c in "+-" else "_" for c in self._ultimo[0])
            f = CARTELLA_IMMAGINI / f"{nome}_{datetime.now():%Y-%m-%d}_{self.v_periodo.get().replace(' ', '')}.png"
            self.fig.savefig(f, dpi=150)
            self.v_stato.set(f"Immagine salvata: {f}")

        def apri_nas(self):
            webbrowser.open(self.dati.fonte.nas + "/")

        def apri_maxi(self):
            r = self.righe.get(self.scelta or "", {})
            if r.get("id"):
                webbrowser.open(f"{M.MAXI}/star_data/{r['id']}/{r['id']}.html")

        def apri_gcn(self):
            if self.scelta:
                import urllib.parse
                q = self.righe[self.scelta].get("nome_maxi") or self.scelta
                webbrowser.open("https://gcn.nasa.gov/circulars?query=" + urllib.parse.quote(f'"{q}"'))

        def diagnostica(self):
            self.scrivi("Diagnostica di rete in corso (può durare 2 minuti)…")
            self.lavora(lambda: self.coda.put(("testo", testo_diagnostica())))

        def chiudi(self):
            self.conf.update(dimensione=self.v_dim.get(), periodo=self.v_periodo.get(),
                             mostra_bat=self.v_bat.get(), geometria=self.geometry())
            try:
                M.salva_conf(self.conf)
            except Exception:
                pass
            self.destroy()

    app = App()
    if autotest:
        return app
    app.mainloop()


# =============================================================================
# DIAGNOSTICA E AUTOTEST
# =============================================================================
def testo_diagnostica():
    righe = [f"{TITOLO} – diagnostica di rete, {datetime.now():%d/%m/%Y %H:%M}"]
    prove = [("MAXI elenco", M.MAXI + "/top/lc_readme.txt", True),
             ("Fermi GBM normale", M.GBM + "/earth_occ/CYGX-1.html", False),
             ("Fermi GBM solo IPv4", M.GBM + "/earth_occ/CYGX-1.html", True),
             ("Swift/BAT", M.BAT + "/BAT_current.html", True),
             ("NAS servizio", M.leggi_conf().get("nas", M.NAS_PREDEFINITO) + "/dati/stato.json", True)]
    for nome, url, ipv4 in prove:
        t0 = time.time()
        try:
            cod, b, _ = M.http_get(url, tempo_max=90 if "GBM" in nome else 10, forza_ipv4=ipv4)
            righe.append(f"{nome}: HTTP {cod}, {len(b) / 1024:.0f} kB in {time.time() - t0:.1f} s")
        except Exception as e:
            righe.append(f"{nome}: ERRORE dopo {time.time() - t0:.1f} s ({type(e).__name__}: {str(e)[:80]})")
    import socket
    try:
        fam = sorted({a[0].name for a in socket.getaddrinfo("batse.msfc.nasa.gov", 443)})
        righe.append(f"Indirizzi di batse.msfc.nasa.gov: {', '.join(fam)}")
    except Exception as e:
        righe.append(f"Indirizzi di batse.msfc.nasa.gov: errore {e}")
    testo = "\n".join(righe)
    try:
        (Path(__file__).resolve().parent / "diagnostica.txt").write_text(testo + "\n", encoding="utf-8")
    except Exception:
        pass
    return testo


def autotest():
    import tempfile
    ok = []

    def verifica(nome, cond):
        ok.append(bool(cond))
        print(("OK   " if cond else "FALLITO ") + nome, flush=True)

    # formati veri (righe copiate dai file del 17/11/2026)
    maxi = ("55057.500000 1.318419 0.040888 0.554480 0.025230 0.475030 0.018563 0.215086 0.021129\n"
            "61361.500000 0.896219 0.060832 0.393610 0.042783 0.352787 0.031143 0.113032 0.026960\n")
    d = M.leggi_maxi(maxi)
    verifica("lettura MAXI", len(d["t"]) == 2 and abs(d["f410"][1] - 0.352787) < 1e-9)
    gbm = ("Name: CYGX-1                   RA:      299.590 Dec:       35.2020\n"
           "Long-term average: 54690.020199  61359.966925  569.3 0.6 635.9 0.9 706.6 1.4 617.2 2.6 98704\n"
           "61359.024436  61359.966925  664.834961  44.089218  815.776978  70.497208  1006.025513  103.168106  1058.292603  179.070831  20\n")
    g = M.leggi_gbm(gbm)
    verifica("lettura Fermi GBM (salta la media)", len(g["t"]) == 1 and abs(g["f2550"][0] - 815.776978) < 1e-6)
    bat = ("#     TIME           RATE          ERROR      YEAR          DAY     STAT_ERR        SYS_ERR   DATA_FLAG\n"
           "      61333       0.16041292     0.00635660   2026         293     0.00255194     0.00561445      0   6976.0 2733.4 6976.0\n")
    b = M.leggi_bat(bat)
    verifica("lettura Swift/BAT in mCrab", len(b["t"]) == 1 and abs(b["f"][0] - 729.15) < 0.1)
    html = ('<tr><td>Cyg X-1</td><td align="center">299.590, 35.202</td><td align="center">'
            '<a href="../star_data/J1958+352/J1958+352.html">&#10004;</a></td></tr>')
    verifica("elenco MAXI", M.leggi_elenco_maxi(html) == [{"nome": "Cyg X-1", "ra": 299.59, "dec": 35.202,
                                                            "id": "J1958+352", "nota": ""}])
    h2 = html.replace("Cyg X-1", "GX 354-0_Slow-Burster with Rapid-Burster")
    e2 = M.leggi_elenco_maxi(h2)[0]
    verifica("nome MAXI con commento attaccato", e2["nome"] == "GX 354-0" and e2["nota"] == "Slow Burster with Rapid Burster")
    verifica("nome GBM", nome_gbm("Cyg X-1", ["CRAB", "CYGX-1"]) == "CYGX-1" and nome_gbm("Crab", ["CRAB"]) == "CRAB")
    # esplosione simulata
    rng = np.random.default_rng(3)
    t = np.arange(60000.5, np.floor(M.mjd_ora()) + 0.5)
    f = 0.06 + rng.normal(0, 0.02, t.size)
    f[-15:] += np.linspace(0.2, 1.5, 15)
    e = np.full(t.size, 0.02)
    sim = {"t": t, "f": f, "e": e, "f24": f * 0.5, "e24": e * 0.6, "f410": f * 0.4, "e410": e * 0.6,
           "f1020": f * 0.1, "e1020": e}
    st = M.stato_sorgente(sim, 1000 / 3.0)
    verifica(f"nuova esplosione riconosciuta ({st['stato']}, dal {st['esplosione']['inizio']})",
             st["stato"] == "nuova")
    sim2 = dict(sim, f=0.06 + rng.normal(0, 0.02, t.size))
    verifica("sorgente calma riconosciuta", M.stato_sorgente(sim2, 1000 / 3.0)["stato"] == "calma")
    # cache e NAS assente: un indirizzo che non esiste deve fallire in fretta, senza bloccarsi
    tmp = Path(tempfile.mkdtemp(prefix="raggi_x_prova_"))
    fo = M.Fonte(nas="http://127.0.0.1:9", cache=tmp, registro=lambda s: None)
    (tmp / "maxi").mkdir()
    (tmp / "maxi" / "X_1day.dat").write_text(maxi)
    (tmp / "maxi" / "X_1day.dat.meta").write_text('{"preso": %f}' % time.time())
    verifica("cache su disco", fo.prendi("maxi/X_1day.dat", "http://127.0.0.1:9/x") == maxi.encode())
    t0 = time.time()
    r = fo.prendi("maxi/Y.dat", "http://127.0.0.1:9/y", tentativi=1)
    verifica(f"NAS e archivio assenti: niente blocco ({time.time() - t0:.1f} s)", r is None and time.time() - t0 < 15)
    verifica("NAS segnato come non attivo", fo.nas_attivo is False)
    # finestra vera con grafico e barra di matplotlib
    try:
        app = avvia_finestra(autotest=True)
        app.dati.scala = 1000 / 3.0
        app.righe["Prova X-1"] = {"id": "J0000+000", "tipo": "buco nero", "nome_maxi": "Prova X-1",
                                  "preferita": True, "stato": st}
        app.riempi_lista()
        app.scelta = "Prova X-1"
        app._disegna("Prova X-1", sim, g, b)
        app.v_dim.set("200 %")
        app.applica_dimensione()
        app.update()
        # lettura dal NAS: un piccolo server locale fa la parte del servizio sul NAS
        import json
        import http.server
        nas = Path(tempfile.mkdtemp(prefix="raggi_x_nas_finto_"))
        (nas / "dati").mkdir()
        (nas / "dati" / "stato.json").write_text('{"versione": "1.0"}')
        (nas / "dati" / "sorgenti.json").write_text(json.dumps({
            "aggiornato_utc": "2026-11-19T12:00:00Z", "scala_crab": 285.0, "sorgenti": [
                {"id": "J1702-487", "nome": "GX 339-4", "tipo": "buco nero", "preferita": True, "stato": "nuova",
                 "oggi": 230.0, "abituale": 3.0, "rapporto": 81.3, "unita": "mCrab", "spiegazione": "dal NAS",
                 "esplosione": {"inizio": "09/11/2026"}},
                {"id": "J1104+382", "nome": "Mrk 421", "tipo": "galassia attiva", "preferita": True,
                 "stato": "pochi"},
                {"id": "J0534+220", "nome": "Crab", "preferita": True, "stato": "calma", "oggi": 1000.0,
                 "abituale": 1000.0, "rapporto": None, "unita": "mCrab"}]}))

        class Zitto(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(nas), **k)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Zitto)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        app.dati.fonte = M.Fonte(nas=f"http://127.0.0.1:{srv.server_address[1]}", cache=tmp,
                                 registro=lambda s: None)
        app._avvio()
        app._leggi_coda()
        verifica("dati presi dal NAS (3 sorgenti, stato già calcolato)",
                 len(app.righe) == 3 and app.righe["GX 339-4"]["stato"]["stato"] == "nuova")
        verifica("righe della lista dal NAS", app._valori("GX 339-4")[:2] == ("NUOVA ESPLOSIONE", "230")
                 and app._valori("Mrk 421")[1] == "" and app._valori("Nebulosa del Granchio")[3] == "")
        verifica("scala della Crab dal NAS", app.dati.scala == 285.0)
        srv.shutdown()
        app.after(2500, app.destroy)
        app.mainloop()
        verifica("finestra con grafici", True)
    except Exception as e:
        traceback.print_exc()
        verifica(f"finestra con grafici ({e})", False)
    print(f"\n{sum(ok)}/{len(ok)} " + ("TUTTO OK" if all(ok) else "CI SONO ERRORI"))
    return all(ok)


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        sys.exit(0 if autotest() else 1)
    elif "--diagnostica" in sys.argv:
        print(testo_diagnostica())
    else:
        avvia_finestra()
