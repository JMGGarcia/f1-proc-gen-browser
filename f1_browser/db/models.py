from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Track(Base):
    __tablename__ = "tracks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    downforce_over_engine = Column(Float, nullable=False)
    car_over_driver = Column(Float, nullable=False)
    target_lap_time = Column(Float, nullable=False, default=90.0)


class Engine(Base):
    __tablename__ = "engines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    power = Column(Float, nullable=False)
    reliability = Column(Float, nullable=False)
    value = Column(Float, nullable=False)
    color_primary = Column(String, nullable=False)
    color_secondary = Column(String, nullable=False)
    nationality = Column(String, nullable=True)


class Sponsor(Base):
    __tablename__ = "sponsors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    tier = Column(String, nullable=False)   # "large", "medium", "small"
    color_primary = Column(String, nullable=False)
    color_secondary = Column(String, nullable=False)
    nationality = Column(String, nullable=True)


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    nationality = Column(String, nullable=True)
    color_primary = Column(String, nullable=False)
    color_secondary = Column(String, nullable=False)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id"), nullable=True)
    sponsor_contract = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    owner_type = Column(String, default="individual", nullable=True)  # "individual" | "engine_supplier" | "sponsor"
    owner_engine_id = Column(Integer, ForeignKey("engines.id"), nullable=True)
    owner_sponsor_id = Column(Integer, ForeignKey("sponsors.id"), nullable=True)
    predecessor_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    finance_base = Column(Integer, nullable=True)  # 1–5; see TeamConstants.FINANCE_BASE_*
    engine_id = Column(Integer, ForeignKey("engines.id"), nullable=True)
    engine_contract = Column(Integer, nullable=True)
    driver_contract_1 = Column(Integer, nullable=True)
    driver_contract_2 = Column(Integer, nullable=True)
    chassis = Column(Float, nullable=True)


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    nationality = Column(String, nullable=False)
    age = Column(Integer, nullable=True)
    skill = Column(Float, nullable=True)
    top_skill = Column(Float, nullable=True)
    retired = Column(Boolean, default=False, nullable=False, index=True)
    retired_season = Column(Integer, nullable=True)
    loyalty = Column(Float, nullable=True)
    greed = Column(Float, nullable=True)
    ambition = Column(Float, nullable=True)
    likability = Column(Float, nullable=True)
    liked_tracks = Column(String, nullable=True)   # comma-separated track DB IDs
    disliked_tracks = Column(String, nullable=True)  # comma-separated track DB IDs


class DriverModifier(Base):
    __tablename__ = "driver_modifiers"

    id                    = Column(Integer, primary_key=True, index=True)
    driver_id             = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    event_def_id          = Column(String, nullable=False)        # JSON "id"
    event_name            = Column(String, nullable=False)
    modifier_json         = Column(Text, nullable=False)           # e.g. '{"skill": -0.3}'
    modifier_type         = Column(String, nullable=False)         # "permanent" | "temporary"
    duration_type         = Column(String, nullable=True)          # "races" | "seasons" | null
    remaining             = Column(Integer, nullable=True)         # null for permanent
    applied_season        = Column(Integer, nullable=False)
    on_expire_action      = Column(String, nullable=True)          # "retire" | null
    on_expire_description = Column(Text, nullable=True)
    active                = Column(Boolean, default=True, nullable=False, index=True)


class Season(Base):
    __tablename__ = "seasons"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, nullable=False, unique=True)
    completed = Column(Boolean, default=False, nullable=False, index=True)


class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=False)
    round_number = Column(Integer, nullable=False)

    season = relationship("Season")
    track = relationship("Track")


class RaceResult(Base):
    __tablename__ = "race_results"

    id = Column(Integer, primary_key=True, index=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    engine_id = Column(Integer, ForeignKey("engines.id"), nullable=False)
    position = Column(Integer, nullable=False)  # 1-based; 0 = DNF
    points = Column(Integer, nullable=False, default=0)
    dnf = Column(Boolean, default=False, nullable=False)
    total_time = Column(Float, nullable=True)  # race time in seconds; None for DNF

    race = relationship("Race")
    driver = relationship("Driver")
    team = relationship("Team")
    engine = relationship("Engine")


class DriverSeasonStats(Base):
    """Snapshot of each driver's state at the end of a season."""

    __tablename__ = "driver_season_stats"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    engine_id = Column(Integer, ForeignKey("engines.id"), nullable=True)
    age = Column(Integer, nullable=False)
    skill = Column(Float, nullable=False)
    top_skill = Column(Float, nullable=False)
    total_points = Column(Integer, nullable=False, default=0)
    championship_position = Column(Integer, nullable=True)
    wins = Column(Integer, nullable=False, default=0)

    driver = relationship("Driver")
    season = relationship("Season")
    team = relationship("Team")
    engine = relationship("Engine")


class TeamSeasonStats(Base):
    """Snapshot of each team's state at the end of a season."""

    __tablename__ = "team_season_stats"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, index=True)
    engine_id = Column(Integer, ForeignKey("engines.id"), nullable=True)
    chassis = Column(Float, nullable=False)
    owner_chief_id  = Column(Integer, ForeignKey("team_chiefs.id"), nullable=True)
    cto_chief_id    = Column(Integer, ForeignKey("team_chiefs.id"), nullable=True)
    cmo_chief_id    = Column(Integer, ForeignKey("team_chiefs.id"), nullable=True)
    cpo_chief_id    = Column(Integer, ForeignKey("team_chiefs.id"), nullable=True)
    owner_skill     = Column(Integer, nullable=True)   # snapshot 0-90
    cto_development = Column(Integer, nullable=True)   # snapshot 0-90
    cto_eng_scouting= Column(Integer, nullable=True)   # snapshot 0-90
    cpo_scouting    = Column(Integer, nullable=True)   # snapshot 0-90
    total_points = Column(Integer, nullable=False, default=0)
    championship_position = Column(Integer, nullable=True)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id"), nullable=True)
    owner_type = Column(String, nullable=True)

    team = relationship("Team")
    season = relationship("Season")
    engine = relationship("Engine")


class EngineSeasonStats(Base):
    """Snapshot of each engine's power/reliability per season."""

    __tablename__ = "engine_season_stats"

    id = Column(Integer, primary_key=True, index=True)
    engine_id = Column(Integer, ForeignKey("engines.id"), nullable=False)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
    power = Column(Float, nullable=False)
    reliability = Column(Float, nullable=False)

    engine = relationship("Engine")
    season = relationship("Season")


class TeamChief(Base):
    __tablename__ = "team_chiefs"

    id               = Column(Integer, primary_key=True)
    first_name       = Column(String, nullable=True)
    last_name        = Column(String, nullable=False)
    nationality      = Column(String, nullable=True)
    role             = Column(String, nullable=False)   # "owner"|"cto"|"cmo"|"cpo"
    age              = Column(Integer, nullable=False)
    skill_primary    = Column(Integer, nullable=False)  # 0-90
    skill_secondary  = Column(Integer, nullable=True)   # CTO only (eng_scouting)
    team_id          = Column(Integer, ForeignKey("teams.id"), nullable=True)
    contract_years   = Column(Integer, nullable=False)  # -1 = owner (no expiry)
    retired          = Column(Boolean, default=False)
    retired_season   = Column(Integer, nullable=True)

    team = relationship("Team", foreign_keys=[team_id])


class WorldMeta(Base):
    __tablename__ = "world_meta"

    id = Column(Integer, primary_key=True)
    world_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WorldEvent(Base):
    __tablename__ = "world_events"

    id = Column(Integer, primary_key=True, index=True)
    season_number = Column(Integer, nullable=False)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=True)
    event_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    race = relationship("Race")


from sqlalchemy import Index  # noqa: E402

class WorldEventEntity(Base):
    __tablename__ = "world_event_entities"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("world_events.id"), nullable=False, index=True)
    entity_type = Column(String, nullable=False)   # "driver"|"team"|"staff"|"engine"|"sponsor"
    entity_id = Column(Integer, nullable=False)

    event = relationship("WorldEvent", backref="entities")

    __table_args__ = (
        Index("ix_world_event_entities_type_id", "entity_type", "entity_id"),
    )
