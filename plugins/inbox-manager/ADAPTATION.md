# Původ a změny

Základ: **Inbox Manager 0.11.4, Authority Hacker**. Původní README a changelog
jsou zachované v [UPSTREAM-README.md](UPSTREAM-README.md) a [CHANGELOG.md](CHANGELOG.md).
Verze **1.0.0** označuje balíček AI Kruhu, nikoli nové vydání původního autora.

Zachováno všech devět skillů, sdílené reference a 21 Python modulů. Automatický
importér marketplace umí jeden skill a pro tento zdroj vrací devět kandidátů.
Proto je zachovaná celá struktura pluginu včetně odkazů mezi skilly a runtime.

Změny AI Kruhu:

- Oprava pozorování vlastních notifikací v `scripts/inbox_engine.py`.
- Oprava otisku vlastních konceptů v `scripts/inbox_snapshot.py`.
- Oprava retenčního úklidu v `scripts/inbox_maintenance.py`: po lhůtě promazat
  také `review_packet`, `checked_content` a `review_owner_answer`; zachovat ochranu
  nevyřešených revizí. Chyba nalezena nezávislou kontrolou před přípravou vydání.
- Přenositelné offline testy všech tří oprav se syntetickými daty.
- Český vstupní návod, profil AI Kruhu a preference Gmail operací přes `gws`.
- Identické manifesty Claude Code a Codexu s verzí 1.0.0 a správcem balíčku AI Kruh.
- V legacy `scripts/inbox_state.py` přejmenována lokální proměnná `token` na
  `authorization_digest`. Jde stále o stejný vypočtený SHA-256, nikoli uložený
  přístupový klíč; úprava odstraňuje falešný poplach scanneru bez změny chování.

Nejsou přibalené osobní profily, přístupy, skutečné zprávy, přílohy, databáze,
logy, provozní evidence ani registr běžících automatizací. Nastavení nového
uživatele vzniká samostatným schváleným setupem.

Připraveno pro soukromý marketplace AI Kruhu. Původní autor zůstává uvedený;
balíček netvrdí, že původní implementaci vytvořil AI Kruh, a nepřiděluje jí novou licenci.

## AI Kruh 1.1.0

Zahrnuje dříve rozpracovaný Gmail dekodér, jeho regresní testy a instrukce pro
trvalý adaptér mimo dočasné složky. Doplněné přenositelné provozní preference:
volitelné zobrazení po jednom e-mailu, minimalistické štítky, důraz na důležité
odpovědi, explicitní pravidla úklidu a přímou zpětnou vazbu. Soukromé příklady,
adresy, podpisy, ID štítků a souhlasy konkrétního vlastníka se nepřenášejí.

Původní runtime a všech devět skillů zůstávají zachované. Prezentaci řídí host
podle schváleného soukromého profilu. Volba `chat_output` váže generovaný plánovací
prompt na hashovaný soubor preferencí; výchozí úplný výstup zachovává staré prompty. Aktualizace
kódu sama nezapíná nové chování. Celý plugin je synchronizován jako celek, protože
jednoskillový aktualizátor vrací devět kandidátů a jeho vynucení by smazalo ostatní
skilly. Existující místní rozpracované soubory zůstaly zachované.
