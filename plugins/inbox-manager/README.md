# Správce e-mailů pro AI Kruh

Naše adaptace **Authority Hacker Inbox Manageru 0.11.4**. Zachovává všech devět skillů a celý lokální runtime. Přidává naše dvě provozní opravy, opravu retence a postup pro Gmail přes Google Workspace CLI (`gws`). [Původní dokumentace](UPSTREAM-README.md) zůstává zachovaná.

## Co dělá

- Prověří e-maily a dostupné podklady, připraví podložené odpovědi.
- Ukáže hotové věci, ověřené koncepty, čekání na protistranu a rozhodnutí pro vlastníka.
- Navrhne, co archivovat a co ponechat v Doručené poště s konkrétním dalším krokem.
- Chrání ruční úpravy konceptů a odděluje oprávnění k návrhům, odesílání a úklidu.
- Umí místní plánované běhy, pokud je hostitelská aplikace podporuje a je dostupná.

## Instalace a první použití

V Claude Code po vydání do marketplace:

```text
/plugin install inbox-manager@ai-kruh-plugins
/inbox
```

V Codexu vyber **Správce e-mailů (Inbox Manager)** z marketplace AI Kruhu a napiš: „Nastav správu mého Gmailu. Zatím pouze koncepty, nic neodesílej.“ Pokud je zároveň zapnutá původní verze Authority Hacker, vypni jednu z obou verzí, aby se nemíchaly stejně pojmenované skilly. Instalace sama nepřenáší profil, nepřepojuje existující plán a nic nemění v Gmailu.

Potřebuješ Python 3.10 nebo novější a funkční `gws` přihlášené ke správnému účtu. Setup ověří konkrétní schopnosti a oprávnění. Instalaci CLI a přihlášení řeš podle oficiální dokumentace nástroje.

## Naše nastavení

Při setupu lze schválit [profil AI Kruhu](skills/inbox/references/ai-kruh-profile.md): celou Doručenou poštu, dávky po 20 změněných konverzacích, nejvýše 5 nových konceptů za běh a 10 denně a ranní běh v 6:00. Volitelně přidat polední a večerní běh, přesná trvalá pravidla úklidu a učení z přímé zpětné vazby. Každá z těchto voleb potřebuje vlastní souhlas. Účet, časové pásmo, týmové adresy a podklady si zvolí každý uživatel sám. Limity konverzací a konceptů kontroluje koordinátor; nejde o tvrdé limity runtime.

Práce s e-mailem používá CLI. Když CLI chybí nebo nemá přístup, plugin pojmenuje problém; na MCP nebo ovládání Gmailu v prohlížeči přejde pouze na výslovný pokyn.

## Práce podle tvého rytmu

Nově lze zvolit chat po jednom e-mailu: dostaneš potřebné souvislosti, doporučení,
odkaz a jednu otázku. Úplný přehled se ukládá soukromě a zobrazí se na požádání.
Dosavadní nastavení výstupů zůstává při aktualizaci zachované.

Plugin navrhuje minimum štítků, rozlišuje skutečné poptávky od cizích formulářových
notifikací a chrání důležité nevyřízené odpovědi před archivací. Jednorázový souhlas
s úklidem nepovažuje za povolení dalších automatických akcí. Člen si určí vlastní
kategorie, týmové výjimky, hlas i rozsah zpětné vazby.

Například: „Chci řešit vždy jeden e-mail, navrhni stručnou odpověď a další krok.
Celý přehled ukaž jen na požádání. Odesílání nech vypnuté.“ Pro existující schránku
si nejprve projdete změnu profilu; instalace sama žádná oprávnění nerozšíří.

Součástí je také [Gmail dekodér](skills/inbox/references/gmail-adapter.md) pro úplné
MIME zprávy a koncepty a postup pro trvalé místní adaptéry. Dekodér není samostatný
plánovač ani hotové připojení Gmailu; skutečný účet a automatické běhy ověří setup.

## Co jsme opravili

1. Vlastní poznámka nebo formulářová notifikace bez cizího účastníka se dá posoudit bez chyby `invalid recipients`. Prázdný seznam adresátů odpovědi dál neprojde.
2. Vlastní koncept se při úplné inventuře nerozpoznává jako nově příchozí zpráva. Ruční úpravy, cizí koncepty a nové odeslané zprávy zůstávají chráněné.
3. Retenční úklid odstraňuje také kopie celého vlákna, kontrolované tělo/MIME a odpověď vlastníka uložené při revizi konceptu. Nedokončené nebo nejisté revize si dál ponechávají podklady k ověření.

Původní instrukce a skripty nejsou zkrácené. Původ a přesné změny popisuje [ADAPTATION.md](ADAPTATION.md). [CHANGELOG.md](CHANGELOG.md) zachovává historii původního produktu.

## Soukromí a omezení

Profily, účty, přístupy, zprávy, databáze a provozní evidence patří do soukromého domova schránky mimo instalovaný plugin i verzované soubory projektu. Balíček je neobsahuje.

Režim `draft_first` původní verze má známé omezení při revizi již existujícího konceptu bez odpovídajícího pravidla pro odpovědi. Když runtime revizi odmítne, zůstane text opravy lokálně a původní koncept beze změny; přehled musí říct, že oprava nebyla zapsaná do Gmailu. Nesmí vzniknout náhradní duplicitní koncept.

Odesílání vyžaduje samostatně schválené oprávnění a skutečně izolovanou nezávislou kontrolu. Bez ní plugin zůstává u konceptů. E-maily ani koncepty automaticky nemaže. Místní plánovač potřebuje dostupný počítač, aplikaci a přihlášení; první automatický běh je nutné ověřit. Offline testy neprokazují funkčnost konkrétní schránky ani plánovače.

## Offline ověření

Z libovolné pracovní složky nastav `PLUGIN_ROOT` na absolutní cestu tohoto pluginu:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 "$PLUGIN_ROOT/tests/test_self_notification.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$PLUGIN_ROOT/tests/test_own_draft_fingerprint.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$PLUGIN_ROOT/tests/test_revision_retention.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$PLUGIN_ROOT/tests/test_gmail_adapter.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$PLUGIN_ROOT/tests/test_chat_output.py"
```

Testy používají syntetická data a dočasnou databázi. Nečtou skutečnou schránku.

Gmail dekodér podporuje bezpečnou revizi jen u jediného prostého textového těla bez
příloh. Existující HTML a multipart koncepty i koncepty s přílohami lze pozorovat,
ale tento adaptér jejich úpravu zablokuje. Původní obsah zůstane zachovaný.
