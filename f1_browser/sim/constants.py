class DriverConstants:
    SKILL_MULTIPLIER = 0.59
    RETIREMENT_AGE = 38
    EARLY_RETIREMENT_AGE = 30
    EARLY_RETIREMENT_CHANCE = 0.2
    FORM_LOW_THRESHOLD = 0.33
    FORM_HIGH_THRESHOLD = 0.66
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
    ENGINE_OWNER_POWER_BONUS = 0.05    # added to engine power per season when supplier owns a team
    DIRECTION_OWNER_FLOOR = 0.40       # minimum direction stat after principal reset (non-individual owners)
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


class SimulationConstants:
    REVOLUTION_EFFECT = 0.5
    REVOLUTION_PROBABILITY = 0.2
    SCOUTING_TRUE_FACTOR = 0.4
    TEAM_DEVELOPMENT_INFLUENCE = 0.1
    YEARLY_CHANGE = 0.1
    HISTORY_YEARS = 5
    RACE_RANDOMNESS = 0.025
    FORM_CHANGE = 0.1
    MAX_TEAMS_PER_ENGINE = 3
    DRIVERS_POOL = 80
    GEN_MIN_AGE = 18
    GEN_MAX_AGE = 23
    MINIMUM_RELIABILITY = 0.4
    MAXIMUM_RELIABILITY = 0.95
    NUMBER_OF_SEASONS = 50


class PointsSystem:
    RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
