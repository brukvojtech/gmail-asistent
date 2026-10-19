# Gmail asistent pro Codex

Veřejný marketplace s jedním pluginem Inbox Manager 1.1.0. Pro stažení není potřeba GitHub účet ani členství v AI Kruhu. Základ Authority Hacker 0.11.4, adaptace a opravy AI Kruhu; původní dokumentace a historie jsou zachované uvnitř pluginu.

## Instalace do Codexu

1. Otevři veřejný repozitář https://github.com/brukvojtech/gmail-asistent.
2. Ověř, že máš Codex a Git. Přihlášení ke GitHubu pro stažení veřejného balíčku není potřeba.
3. Přidej marketplace v Codexu. V terminálu lze použít:

```sh
codex plugin marketplace add brukvojtech/gmail-asistent
codex plugin add inbox-manager@gmail-asistent
```

Případně použij v aplikaci nabídku pluginů, přidání marketplace z GitHub URL a instalaci Správce e-mailů.

4. Vytvoř vlastní pracovní složku pro správu pošty. Potom napiš:

> Nastav správu mého Gmailu. Nejdřív ověř přihlášený účet a domluv se mnou rozsah, styl a priority. Zatím pouze přehled a koncepty. Nic neodesílej, nearchivuj ani nemaž bez mého souhlasu. Používej Google Workspace CLI. Proveď mě setupem pluginu.

## Požadavky

- Codex s podporou pluginů, Python 3.10+ a Google Workspace CLI `gws`.
- Vlastní přihlášení k cílovému Google účtu. Existující připojení nejdřív ověřit, nepřepisovat ho automaticky.
- Spark může dál sloužit jako klient stejné Gmail schránky. Synchronizaci konceptů je nutné prakticky ověřit.
- Místní plán potřebuje dostupný počítač a běžící Codex. Instalace sama nic neplánuje ani nemění v Gmailu.

## Co se přenáší

Pouze plugin, návody a syntetické testy. Osobní profily, firemní znalosti, e-maily, přístupy, databáze a běžící automatizace se nepřenášejí. Nastavení každého uživatele vznikne samostatně u něj.

## Společné zprovoznění

Viz [CHECKLIST.md](CHECKLIST.md). První použití a úspěšný plánovaný běh ověřit na tvém počítači; offline kontrola balíčku je nenahrazuje.
