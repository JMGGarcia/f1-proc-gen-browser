class DriverConstants:
    SKILL_MULTIPLIER = 0.59
    RETIREMENT_AGE = 38
    EARLY_RETIREMENT_AGE = 30
    EARLY_RETIREMENT_CHANCE = 0.2
    SKILL_IMPROVEMENT_RATE = 0.08
    MIN_SKILL = 0.1
    # Trait weights for driver utility scoring (must sum to 1 for normalised scores)
    LOYALTY_WEIGHT = 1 / 3
    GREED_WEIGHT = 1 / 3
    AMBITION_WEIGHT = 1 / 3


class TeamConstants:
    MAX_DRIVERS = 2
    MIN_CHASSIS = 0.0
    MAX_CHASSIS = 1.0
    ENGINE_OWNER_POWER_BONUS = 0.025   # added to engine power per season when supplier owns a team
    ENGINE_SUPPLIER_REVOLUTION_MIN = 0.6  # minimum power floor after revolution for supplier-owned engines
    ENGINE_IN_USE_REVOLUTION_MIN = 0.4   # minimum power floor after revolution for any engine supplying a team
    STRUGGLING_THRESHOLD = 5           # constructors position outside top-N to count as poor season
    SALE_PROBABILITY = 0.65
    BUYER_WEIGHTS = {"individual": 60, "engine_supplier": 30, "sponsor": 10}
    MAX_ENGINE_SUPPLIER_OWNERS = 4
    MAX_SPONSOR_OWNERS = 3
    ENGINE_OWNER_CONTRACT = 10         # long lock-in contract years when engine supplier buys team
    SPONSOR_OWNER_CONTRACT = 10
    # Finance base by owner type (individual range set at generation time)
    FINANCE_BASE_INDIVIDUAL_MIN = 1
    FINANCE_BASE_INDIVIDUAL_MAX = 3
    FINANCE_BASE_SPONSOR_OWNER = 4
    FINANCE_BASE_ENGINE_SUPPLIER = 5
    # Chief system
    CHIEF_VACANCY_FLOOR = 0.40
    OWNER_RETIRE_AGE = 70
    CHIEF_RETIRE_AGE = 65
    CHIEF_RETIRE_PROB_60 = 0.80        # prob of retiring as free agent after age 60
    CHIEF_GEN_MIN_AGE_OWNER = 40
    CHIEF_GEN_MAX_AGE_OWNER = 60
    CHIEF_GEN_MIN_AGE = 40
    CHIEF_GEN_MAX_AGE = 50
    CHIEF_GEN_SKILL_MIN = 0
    CHIEF_GEN_SKILL_MAX = 90
    CHIEF_CONTRACT_MIN = 2
    CHIEF_CONTRACT_MAX = 5
    CHIEF_SUCCESSOR_SKILL_MAX = 70
    CHIEFS_POOL_PER_ROLE = 13          # free-agent pool size per non-owner role (CTO/CMO/CPO)


class SimulationConstants:
    REVOLUTION_EFFECT = 0.5
    REVOLUTION_PROBABILITY = 0.2
    SCOUTING_TRUE_FACTOR = 0.4
    TEAM_DEVELOPMENT_INFLUENCE = 0.1
    YEARLY_CHANGE = 0.1
    HISTORY_YEARS = 5
    RACE_RANDOMNESS = 0.025
    MAX_TEAMS_PER_ENGINE = 3
    DRIVERS_POOL = 80
    GEN_MIN_AGE = 18
    GEN_MAX_AGE = 23
    MINIMUM_RELIABILITY = 0.4
    MAXIMUM_RELIABILITY = 0.95
    NUMBER_OF_SEASONS = 10


class PointsSystem:
    RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


class RaceConstants:
    TRACK_PREFERENCE_BONUS = 0.05
    QUALIFYING_NOISE_FACTOR = 0.5
    PIT1_LAP_RANGE = (10, 20)
    PIT2_LAP_RANGE = (30, 40)
    START_GAP_SECONDS = 0.2
    LAP_TIME_SPREAD = 10.0
    LAP_TIME_NOISE_STDDEV = 0.15
    LAP_TIME_FLOOR_PCT = 0.93
    INCIDENT_PROB_DIVISOR = 50.0
    MAJOR_INCIDENT_PROB = 0.4


class SponsorConstants:
    COLOR_DISTANCE_THRESHOLD = 80
    RENEWAL_PROBABILITY = 0.75
    CONTRACT_LARGE_MIN = 4
    CONTRACT_LARGE_MAX = 6
    CONTRACT_MEDIUM_MIN = 3
    CONTRACT_MEDIUM_MAX = 5
    CONTRACT_SMALL_MIN = 2
    CONTRACT_SMALL_MAX = 4


class WorldConstants:
    FALLBACK_POSITION = 999
    CHASSIS_ENGINE_RANDOM_FACTOR = 0.3
    ENGINE_RELIABILITY_REVOLUTION_DELTA_MIN = 0.3
    ENGINE_RELIABILITY_REVOLUTION_DELTA_RANGE = 0.2
    CONTRACT_YEARS_PROB_2 = 0.02
    CONTRACT_YEARS_PROB_3 = 0.2
    CONTRACT_YEARS_PROB_4 = 0.8
    CONTRACT_YEARS_PROB_5 = 0.95


from enum import Enum


class EventType(str, Enum):
    RACE_START = "race_start"
    RACE_LAP = "race_lap"
    CHIEF_DEBUT = "chief_debut"
    CHIEF_SUCCESSION = "chief_succession"
    CHIEF_FREE_AGENT = "chief_free_agent"
    CHIEF_SIGNING = "chief_signing"
    DRIVER_RETIREMENT = "driver_retirement"
    DRIVER_DEBUT = "driver_debut"
    DRIVER_TRANSFER = "driver_transfer"
    FORMULA_REVOLUTION = "formula_revolution"
    ENGINE_DEAL = "engine_deal"
    TEAM_SALE = "team_sale"
    DRIVER_CHAMPION = "driver_champion"
    TEAM_CHAMPION = "team_champion"
    SPONSOR_DEAL = "sponsor_deal"
    DRIVER_EVENT = "driver_event"
    DRIVER_EVENT_EXPIRED = "driver_event_expired"
