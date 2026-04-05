# Unicode flag emoji for each nationality code used by the sim
# Deprecated: use sim.countries module instead

from sim.countries import COUNTRIES

NATIONALITY_FLAGS: dict[str, str] = {code: country.flag for code, country in COUNTRIES.items()}
