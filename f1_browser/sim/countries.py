"""Country definitions and registry for the F1 simulation."""

from __future__ import annotations


class Country:
    """Represents a country with characteristics that can evolve."""
    
    def __init__(
        self,
        code: str,
        name: str,
        flag: str,
        population: int = 5,
        millionaires: int = 5,
        interest: int = 5,
        infrastructure: int = 5,
    ):
        self.code = code
        self.name = name
        self.flag = flag
        self.population = population              # 1-10: wealth/population base
        self.millionaires = millionaires          # 1-10: wealthy individuals (F1 funding)
        self.interest = interest                  # 1-10: local interest in F1
        self.infrastructure = infrastructure      # 1-10: car building/development capability
    
    def __repr__(self) -> str:
        return f"Country({self.code})"
    
    def __eq__(self, other) -> bool:
        if isinstance(other, Country):
            return self.code == other.code
        return self.code == other
    
    def __hash__(self) -> int:
        return hash(self.code)
    
    @property
    def driver_weight(self) -> float:
        """Pre-computed weight for driver generation (all 4 characteristics)."""
        return (self.population + self.millionaires + self.interest + self.infrastructure) / 4
    
    @property
    def team_weight(self) -> float:
        """Pre-computed weight for team generation (millionaires, interest, infrastructure - no population)."""
        return (self.millionaires + self.interest + self.infrastructure) / 3


# Repository of all countries
COUNTRIES = {
    "PT": Country("PT", "Portugal", "🇵🇹", population=3, millionaires=3, interest=10, infrastructure=7),
    "EN": Country("EN", "England", "🇬🇧", population=6, millionaires=7, interest=10, infrastructure=10),
    "FR": Country("FR", "France", "🇫🇷", population=6, millionaires=7, interest=10, infrastructure=10),
    "GE": Country("GE", "Germany", "🇩🇪", population=6, millionaires=7, interest=10, infrastructure=10),
    "ES": Country("ES", "Spain", "🇪🇸", population=6, millionaires=6, interest=10, infrastructure=10),
    "AU": Country("AU", "Australia", "🇦🇺", population=4, millionaires=6, interest=10, infrastructure=8),
    "IT": Country("IT", "Italy", "🇮🇹", population=6, millionaires=6, interest=10, infrastructure=10),
    "JP": Country("JP", "Japan", "🇯🇵", population=7, millionaires=7, interest=10, infrastructure=10),
    "RU": Country("RU", "Russia", "🇷🇺", population=7, millionaires=5, interest=5, infrastructure=2),
    "BR": Country("BR", "Brazil", "🇧🇷", population=7, millionaires=3, interest=9, infrastructure=6),
    "NL": Country("NL", "Netherlands", "🇳🇱", population=4, millionaires=7, interest=10, infrastructure=8),
    "FI": Country("FI", "Finland", "🇫🇮", population=3, millionaires=5, interest=10, infrastructure=9),
    "MX": Country("MX", "Mexico", "🇲🇽", population=7, millionaires=3, interest=6, infrastructure=3),
    "US": Country("US", "United States", "🇺🇸", population=8, millionaires=10, interest=2, infrastructure=7),
    "IN": Country("IN", "India", "🇮🇳", population=10, millionaires=2, interest=1, infrastructure=1),
    "AR": Country("AR", "Argentina", "🇦🇷", population=6, millionaires=3, interest=10, infrastructure=2),
    "SE": Country("SE", "Sweden", "🇸🇪", population=3, millionaires=5, interest=8, infrastructure=7),
    "KR": Country("KR", "South Korea", "🇰🇷", population=6, millionaires=6, interest=2, infrastructure=6),
    "CN": Country("CN", "China", "🇨🇳", population=10, millionaires=9, interest=1, infrastructure=4),
    "CH": Country("CH", "Switzerland", "🇨🇭", population=3, millionaires=6, interest=6, infrastructure=4),
    "CA": Country("CA", "Canada", "🇨🇦", population=5, millionaires=7, interest=8, infrastructure=8),
    "NZ": Country("NZ", "New Zealand", "🇳🇿", population=2, millionaires=4, interest=7, infrastructure=4),
    "ZA": Country("ZA", "South Africa", "🇿🇦", population=6, millionaires=3, interest=5, infrastructure=2),
}


def get_country(code: str) -> Country | None:
    """Get a country by code, returning None if not found."""
    return COUNTRIES.get(code)


def get_all_countries() -> list[Country]:
    """Get all available countries."""
    return list(COUNTRIES.values())
