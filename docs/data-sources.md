# Data sources and acknowledgements

This repository contains **no data**: everything is downloaded at run time from the public archives below, which
remain under the terms of their providers. If you show or publish results obtained with this program, please credit
the data as indicated.

## MAXI (Monitor of All-sky X-ray Image) — main source

* JAXA/RIKEN instrument on the International Space Station; the Gas Slit Camera scans almost the whole sky every
  92-minute orbit.
* Used: daily light curves `https://maxi.riken.jp/star_data/<ID>/<ID>_g_lc_1day_all.dat` (9 columns: MJD, 2–20 keV
  flux and error, then 2–4, 4–10 and 10–20 keV, in photons s⁻¹ cm⁻²) and the source list
  `https://maxi.riken.jp/top/slist.html` (457 sources in November 2026).
* Caveats: not every source is observed every day (gaps of ~10 days happen); the wide point-spread function blends
  nearby sources (see GX 354-0 / Rapid Burster in [how-it-works.md](how-it-works.md)).
* **Acknowledgement:** "This research has made use of MAXI data provided by RIKEN, JAXA and the MAXI team."
  Reference: Matsuoka, M. et al. 2009, PASJ, 61, 999.

## Fermi GBM Earth Occultation — hard X-rays

* Fermi Gamma-ray Burst Monitor, NASA Marshall Space Flight Center; fluxes measured from the step in the count rate
  when a source sets behind or rises from the Earth.
* Used: `https://batse.msfc.nasa.gov/gbm/science/earth_occ/lightcurves/<NAME>_GBM_occ_fluxhist_auto.txt` (fluxes in
  mCrab in 12–25, 25–50, 50–100 and 100–300 keV) and the source list `.../earth_occ.html`. Quick-look products,
  about 2 days behind. Requested over IPv4 only (see the network notes).
* Reference: Wilson-Hodge, C. A. et al. 2012, ApJS, 201, 33 ("Three years of Fermi GBM Earth Occultation Monitoring").

## Swift/BAT Hard X-ray Transient Monitor — history only

* Swift ended its science operations on 23 October 2026; its last observation was on 20 October 2026. The program
  downloads each light curve once (15–50 keV, from 2005).
* Used: `https://swift.gsfc.nasa.gov/results/transients/<Name>.lc.txt` (fainter sources under `weak/`).
* **Acknowledgement requested by the BAT team:** "Swift/BAT transient monitor results provided by the Swift/BAT
  team." Reference: Krimm, H. A. et al. 2013, ApJS, 209, 14.

## The Astronomer's Telegram and GCN Circulars — news

* ATel RSS feed `https://www.astronomerstelegram.org/?rss`; GCN Circulars `https://gcn.nasa.gov/circulars/<n>.json`.
* Only titles, dates, links and the first lines are stored (last 400 items); the page links to the originals.

## HEASARC

Used only by the network check (`tools/`), to verify that its TAP service answers (it returns binary VOTables).
Links to Chandra/XMM-Newton/NICER observations of each source are planned.

## Software

Python, NumPy and Matplotlib. The icon is drawn by [`pc/crea_icona.py`](../pc/crea_icona.py) (original artwork,
same license as the code).
