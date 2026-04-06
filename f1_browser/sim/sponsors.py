from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sim.teams import Team
    from sim.countries import Country

from sim.countries import get_country


class Sponsor:
    def __init__(
        self,
        name: str,
        tier: str,           # "large", "medium", "small"
        color_primary: str,
        color_secondary: str,
        db_id: int = 0,
        nationality: Country | str | None = None,
    ):
        self.db_id = db_id
        self.name = name
        self.tier = tier
        self.color_primary = color_primary
        self.color_secondary = color_secondary
        h = color_primary.lstrip("#")
        self.rgb_primary: tuple[int, int, int] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        
        # Handle both Country objects and string codes
        if isinstance(nationality, str):
            self.country = get_country(nationality)
        else:
            self.country = nationality
        
        self.team: Optional[Team] = None
    
    @property
    def nationality(self) -> str | None:
        """Return nationality code for database storage."""
        return self.country.code if self.country else None

    def assign_team(self, team: Team):
        self.team = team

    def release_team(self):
        self.team = None


# ------------------------------------------------------------------
# Sponsor catalogue — realistic F1 sponsors with approximate colors
# tier: "large" (top 3 teams only), "medium" (top half), "small" (any)
# ------------------------------------------------------------------
SPONSOR_DATA = [
    # (name, tier, color_primary, color_secondary, nationality)
    # ── Large ────────────────────────────────────────────────────
    ("Red Bull",            "large",  "#1C2E6E", "#CC2229", "AT"),
    ("Shell",               "large",  "#DD1D21", "#FBC817", "NL"),
    ("Aramco",              "large",  "#008A3D", "#F0F0F0", "SA"),
    ("Qatar Airways",       "large",  "#5C0632", "#F0F0F0", "QA"),
    ("Rolex",               "large",  "#006039", "#C9A84C", "CH"),
    ("Heineken",            "large",  "#1A5C28", "#F0F0F0", "NL"),
    ("Oracle",              "large",  "#C74634", "#F0F0F0", "US"),
    ("AWS",                 "large",  "#232F3E", "#FF9900", "US"),
    ("Etihad Airways",      "large",  "#C5A028", "#1C1C1C", "AE"),
    ("Emirates",            "large",  "#D71A21", "#C5A028", "AE"),
    ("Qualcomm",            "large",  "#3253DC", "#F0F0F0", "US"),
    ("Visa",                "large",  "#1A1F71", "#F7B600", "US"),
    ("Mastercard",          "large",  "#EB001B", "#F79E1B", "US"),
    ("Petronas",            "large",  "#00A19B", "#111111", None),   # Malaysian
    ("Louis Vuitton",       "large",  "#1A1A1A", "#C9A84C", "FR"),
    ("Samsung",             "large",  "#1428A0", "#F0F0F0", "KR"),
    ("Lucky Strike",        "large",  "#CC0000", "#F5DEB3", "US"),
    ("Marlboro",            "large",  "#FF0000", "#FFFFFF", "US"),
    ("Phillip Morris",      "large",  "#1E90FF", "#FFFFFF", "US"),
    ("Chesterfield",        "large",  "#FF8700", "#000000", "US"),
    ("Camel",               "large",  "#D4AF37", "#003087", "US"),
    ("Newport",             "large",  "#20B2AA", "#000000", "US"),
    ("Winston",             "large",  "#00008B", "#FFFFFF", "US"),
    ("West",                "large",  "#8B0000", "#C0C0C0", "GE"),
    ("Rothmans",            "large",  "#00005F", "#FFD700", "EN"),
    ("Pall Mall",           "large",  "#006400", "#FFFFFF", "EN"),
    ("Microsoft",           "large",  "#0078D4", "#F0F0F0", "US"),
    ("Google",              "large",  "#1A73E8", "#FBBC04", "US"),
    ("Alibaba",             "large",  "#FF6B6B", "#F0F0F0", "CN"),
    ("Nestle",              "large",  "#8B4513", "#F0F0F0", "CH"),
    ("Huawei",              "large",  "#CF0A2C", "#F0F0F0", "CN"),
    # ── Medium ───────────────────────────────────────────────────
    ("Santander",           "medium", "#EC0000", "#F0F0F0", "ES"),
    ("LVMH",                "medium", "#1A1A1A", "#C9A84C", "FR"),
    ("Xbox",                "medium", "#107C10", "#F0F0F0", "US"),
    ("Castrol",             "medium", "#006135", "#D40000", "EN"),
    ("TAG Heuer",           "medium", "#0D0D0D", "#CA0814", "CH"),
    ("Vodafone",            "medium", "#E60000", "#F0F0F0", "EN"),
    ("DHL",                 "medium", "#FFCC00", "#D40511", "GE"),
    ("UBS",                 "medium", "#E30613", "#1A1A1A", "CH"),
    ("Martini",             "medium", "#E31836", "#F0F0F0", "IT"),
    ("Monster Energy",      "medium", "#0D0D0D", "#3DD300", "US"),
    ("Telefonica",          "medium", "#0D3580", "#F0F0F0", "ES"),
    ("Mobil 1",             "medium", "#E2231A", "#003087", "US"),
    ("Hugo Boss",           "medium", "#1A1A1A", "#C9A84C", "GE"),
    ("Johnnie Walker",      "medium", "#000000", "#C9A84C", "EN"),
    ("ING",                 "medium", "#FF6200", "#F0F0F0", "NL"),
    ("HSBC",                "medium", "#DB0011", "#F0F0F0", "EN"),
    ("SAP",                 "medium", "#1C3557", "#F0AB00", "GE"),
    ("SoftBank",            "medium", "#CC0000", "#F0F0F0", "JP"),
    ("Richard Mille",       "medium", "#0D0D0D", "#CA0814", "FR"),
    ("Puma",                "medium", "#1A1A1A", "#F0F0F0", "GE"),
    ("Tommy Hilfiger",      "medium", "#003087", "#CC0000", "US"),
    ("Hackett",             "medium", "#1C2D5E", "#F0F0F0", "EN"),
    ("Claro",               "medium", "#DA291C", "#F0F0F0", "MX"),
    ("Telmex",              "medium", "#005DA8", "#F0F0F0", "MX"),
    ("Singha",              "medium", "#003087", "#FFD700", None),
    ("Rakuten",             "medium", "#BF0000", "#F0F0F0", "JP"),
    ("NetApp",              "medium", "#005073", "#F0F0F0", "US"),
    ("BWT",                 "medium", "#E91E8C", "#F0F0F0", "AT"),
    ("Unilever",            "medium", "#003087", "#F0F0F0", "NL"),
    ("NEC",                 "medium", "#003087", "#F0F0F0", "JP"),
    ("Accenture",           "medium", "#A100FF", "#F0F0F0", "US"),
    ("Capgemini",           "medium", "#0070AD", "#F0F0F0", "FR"),
    ("Tata",                "medium", "#003087", "#F0F0F0", "IN"),
    ("Gulf Oil",            "medium", "#F47920", "#003087", "US"),
    ("Esso",                "medium", "#CC0000", "#003087", "US"),
    ("Randstad",            "medium", "#2175D9", "#F0F0F0", "NL"),
    ("Nvidia",              "medium", "#76B900", "#1A1A1A", "US"),
    ("Acronis",             "medium", "#CF0A2C", "#F0F0F0", "CH"),
    ("MSC Cruises",         "medium", "#003A70", "#F0F0F0", "CH"),
    ("Crowdstrike",         "medium", "#E3172D", "#F0F0F0", "US"),
    ("BP",                  "medium", "#009A44", "#FFD100", "EN"),
    ("Total",               "medium", "#C8102E", "#F08000", "FR"),
    ("HP",                  "medium", "#0171AD", "#F0F0F0", "US"),
    ("Salesforce",          "medium", "#1798C1", "#F0F0F0", "US"),
    ("Sony",                "medium", "#1A1A1A", "#F0F0F0", "JP"),
    ("Panasonic",           "medium", "#003087", "#F0F0F0", "JP"),
    ("Toshiba",             "medium", "#CC0000", "#F0F0F0", "JP"),
    ("Intel",               "medium", "#0071C5", "#F0F0F0", "US"),
    ("Cisco",               "medium", "#1BA0D7", "#F0F0F0", "US"),
    ("Qualys",              "medium", "#E4002B", "#F0F0F0", "US"),
    ("Databricks",          "medium", "#FF3621", "#F0F0F0", "US"),
    ("Benetton",            "medium", "#00A651", "#F0F0F0", "IT"),
    ("PlayStation",         "medium", "#003087", "#F0F0F0", "JP"),
    ("Elf",                 "medium", "#0055A4", "#FFD700", "FR"),
    ("Agip",                "medium", "#CC0000", "#FFD700", "IT"),
    ("Valvoline",           "medium", "#CC0000", "#F0F0F0", "US"),
    ("Tencent",             "medium", "#0084FF", "#F0F0F0", "CN"),
    ("BYD",                 "medium", "#CC0000", "#FFFFFF", "CN"),
    ("Roche",               "medium", "#CC0000", "#F0F0F0", "CH"),
    ("Swatch",              "medium", "#1A1A1A", "#C9A84C", "CH"),
    ("ABB",                 "medium", "#003087", "#FFD700", "CH"),
    ("Novartis",            "medium", "#003087", "#F0F0F0", "CH"),
    ("Bombardier",          "medium", "#003087", "#FFD700", "CA"),
    # ── Small ────────────────────────────────────────────────────
    ("Crypto.com",          "small",  "#002D74", "#F0F0F0", None),
    ("Binance",             "small",  "#F3BA2F", "#1A1A1A", None),
    ("Lego",                "small",  "#D01012", "#FFD700", "DK"),
    ("Lenovo",              "small",  "#E2231A", "#F0F0F0", "CN"),
    ("Moët & Chandon",      "small",  "#C6A84B", "#1A1A1A", "FR"),
    ("Bridgestone",         "small",  "#CC0000", "#1A1A1A", "JP"),
    ("Brembo",              "small",  "#E32119", "#F0F0F0", "IT"),
    ("Corona",              "small",  "#003087", "#FFCC00", "MX"),
    ("Lucozade",            "small",  "#FF7400", "#F0F0F0", "EN"),
    ("NGK",                 "small",  "#8B0000", "#C9A84C", "JP"),
    ("Singtel",             "small",  "#CC0033", "#F0F0F0", None),   # Singaporean
    ("Jack Daniel's",       "small",  "#1A1A1A", "#F0F0F0", "US"),
    ("Schweppes",           "small",  "#F5C400", "#1A1A1A", "EN"),
    ("Infosys",             "small",  "#006DAA", "#F0F0F0", "IN"),
    ("Cognizant",           "small",  "#0033A0", "#F0F0F0", "US"),
    ("Dell",                "small",  "#007DB8", "#F0F0F0", "US"),
    ("Burn",                "small",  "#1A1A1A", "#FF4400", None),
    ("Credit Suisse",       "small",  "#0C2C7C", "#F0F0F0", "CH"),  
    ("Pirtek",              "small",  "#E4002B", "#F0F0F0", "AU"),
    ("OMP",                 "small",  "#1A1A1A", "#F0F0F0", "IT"),
    ("Sparco",              "small",  "#E4002B", "#F0F0F0", "IT"),
    ("Alpinestars",         "small",  "#1A1A1A", "#CC0000", "IT"),
    ("Bell Helmets",        "small",  "#1C3557", "#F0F0F0", "US"),
    ("Arai",                "small",  "#CC0000", "#F0F0F0", "JP"),
    ("Shoei",               "small",  "#1A1A1A", "#FF6600", "JP"),
    ("Sabelt",              "small",  "#CC0000", "#F0F0F0", "IT"),
    ("Recaro",              "small",  "#1A1A1A", "#CC0000", "GE"),
    ("Dunlop",              "small",  "#FFD700", "#1A1A1A", "EN"),
    ("Yokohama",            "small",  "#003087", "#F0F0F0", "JP"),
    ("Federal Mogul",       "small",  "#003087", "#F0F0F0", "US"),
    ("Gates",               "small",  "#CC0000", "#F0F0F0", "US"),
    ("SKF",                 "small",  "#003087", "#FFD700", "SE"),
    ("Mahle",               "small",  "#CC0000", "#F0F0F0", "GE"),
    ("Bosch",               "small",  "#CC0000", "#F0F0F0", "GE"),
    ("Continental",         "small",  "#FFA500", "#1A1A1A", "GE"),
    ("ZF",                  "small",  "#003087", "#F0F0F0", "GE"),
    ("Hankook",             "small",  "#FF6600", "#1A1A1A", "KR"),
    ("Delphi",              "small",  "#003087", "#F0F0F0", "US"),
    ("Denso",               "small",  "#003087", "#F0F0F0", "JP"),
    ("Enkei",               "small",  "#1A1A1A", "#C9A84C", "JP"),
    ("OZ Racing",           "small",  "#CC0000", "#F0F0F0", "IT"),
    ("Sparkling Hill",      "small",  "#4A90D9", "#F0F0F0", None),
    ("Nespresso",           "small",  "#1A1A1A", "#8B0000", "CH"), 
    ("Lavazza",             "small",  "#003087", "#FFD700", "IT"),
    ("Julius Baer",         "small",  "#003087", "#C9A84C", "CH"),
    ("Banque de France",    "small",  "#003087", "#C9A84C", "FR"),
    ("ANZ",                 "small",  "#007DBA", "#F0F0F0", "AU"),
    ("Chandon",             "small",  "#1A1A1A", "#C9A84C", "FR"),
    ("Oakley",              "small",  "#1A1A1A", "#FF6600", "US"),
    ("Ray-Ban",             "small",  "#1A1A1A", "#C9A84C", "IT"),
    ("Fossil",              "small",  "#8B6914", "#F0F0F0", "US"),
    ("Tissot",              "small",  "#CC0000", "#F0F0F0", "CH"), 
    ("Hublot",              "small",  "#1A1A1A", "#C9A84C", "CH"), 
    ("IWC",                 "small",  "#1A1A1A", "#C9A84C", "CH"),
    ("Omega",               "small",  "#003087", "#C9A84C", "CH"),
    ("Casio",               "small",  "#003087", "#F0F0F0", "JP"),
    ("Seiko",               "small",  "#1A1A1A", "#FFD700", "JP"),
    ("GoPro",               "small",  "#1A1A1A", "#00AEEF", "US"),
    ("Epson",               "small",  "#003087", "#F0F0F0", "JP"),
    ("Canon",               "small",  "#CC0000", "#F0F0F0", "JP"),
    ("Nikon",               "small",  "#FFD700", "#1A1A1A", "JP"),
    ("LG",                  "small",  "#A50034", "#F0F0F0", "KR"),
    ("Xiaomi",              "small",  "#FF6900", "#F0F0F0", "CN"),
    ("PayPal",              "small",  "#003087", "#009CDE", "US"),
    ("Stripe",              "small",  "#635BFF", "#F0F0F0", "US"),
    ("Coinbase",            "small",  "#0052FF", "#F0F0F0", "US"),
    ("Palantir",            "small",  "#1A1A1A", "#F0F0F0", "US"),
    ("AMD",                 "small",  "#CC0000", "#F0F0F0", "US"),
    ("VMware",              "small",  "#607078", "#F0F0F0", "US"),
    ("Elastic",             "small",  "#F04E98", "#F0F0F0", "US"),
    ("Datadog",             "small",  "#632CA6", "#F0F0F0", "US"),
    ("SEGA",                "small",  "#1A1A1A", "#0066CC", "JP"),
    ("Goodyear",            "small",  "#FFD700", "#1A1A1A", "US"),
    ("Parmalat",            "small",  "#003087", "#F0F0F0", "IT"),
    ("Kenwood",             "small",  "#CC0000", "#F0F0F0", "JP"),
    ("Pioneer",             "small",  "#CC0000", "#F0F0F0", "JP"),
    ("National",            "small",  "#003087", "#F0F0F0", "BR"),
    ("Haier",               "small",  "#003087", "#F0F0F0", "CN"),
    ("TCL",                 "small",  "#CC0000", "#F0F0F0", "CN"),
    ("Lindt",               "small",  "#8B6914", "#F0F0F0", "CH")
]
