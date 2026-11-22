# Changelog — X-rays from Black Holes (Raggi X dai buchi neri)

## 1.1 — 2026-11-19
* MAXI sometimes appends a comment to a source name (`GX 354-0_Slow-Burster with Rapid-Burster`): the name is now
  only `GX 354-0` and the comment goes into the description.
* Notes for GX 354-0 and H 1730-333: MAXI does not separate the Slow Burster from the Rapid Burster (1.5° apart).
* News are matched also through alternative names (MXB 1730-335, Rapid Burster, RX J0440.9+4431, 4U 1728-34, …).
* Notification text fixed. At start-up the server rebuilds at once, from the files already downloaded, the entries
  with the old names; outbursts already notified are not notified again.

## 1.0 — 2026-11-19
* First complete version: Docker server (MAXI for all ~457 sources every 6 h, Fermi GBM daily over IPv4, Swift/BAT
  history once, ATel + GCN news every 30 min), states "new outburst / in outburst / rising / fading / usual / too
  faint / old data" with outburst start and peak, `eventi.json`, sky map, plots on request, Italian web page with
  adjustable text size; desktop app reading everything from the server (or from the archives when the server is not
  reachable); desktop notifier; icon; `installa.sh`.
* Same day: page layout switches to one column with large text and tables scroll inside their box; "×" column
  renamed "Volte il solito"; rows of buttons in the window wrap instead of being cut.
* First real detections on the first full cycle: GX 354-0 / Rapid Burster (443 mCrab from 14 Nov 2026) and
  LS V +44 17 (44 mCrab from 11 Nov 2026).

## 0.2 — 2026-11-17 (desktop prototype)
* Larger arrows, check boxes, scroll bars and buttons (ttk "clam" theme), Italian buttons instead of the tiny
  Matplotlib toolbar, plot fonts scaled with the text size.
* Diagnosis: Fermi GBM was slow because of IPv6 (90.7 s) — IPv4 only: 0.6 s.

## 0.1 — 2026-11-17 (desktop prototype)
* Network check of all archives (31/31 reachable). Found that Swift ended its observations on 20 Oct 2026.
* First window: 17 favourite sources or all MAXI sources, light curves MAXI + Fermi GBM + Swift/BAT, hardness ratio,
  hardness–intensity diagram.
