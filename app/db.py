from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .config import DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)


@event.listens_for(engine, "connect")
def _enable_fk(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    broker = Column(String, nullable=False)
    account_hash = Column(String, nullable=False, unique=True)
    account_number_masked = Column(String)
    account_type = Column(String)
    nickname = Column(String)
    last_synced_at = Column(DateTime)

    positions = relationship(
        "Position", back_populates="account", cascade="all, delete-orphan"
    )


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String, nullable=False)
    description = Column(Text)
    asset_type = Column(String)
    quantity = Column(Float, nullable=False)
    market_value = Column(Float)
    last_price = Column(Float)
    cost_basis_price = Column(Float)
    cost_basis_value = Column(Float)

    account = relationship("Account", back_populates="positions")


class ChainAddress(Base):
    __tablename__ = "chain_addresses"
    id = Column(Integer, primary_key=True)
    chain = Column(String, nullable=False)
    address = Column(String, nullable=False, unique=True)
    label = Column(String)


class BitcoinLoan(Base):
    __tablename__ = "bitcoin_loans"
    id = Column(Integer, primary_key=True)
    origination_date = Column(Date, nullable=False)
    termination_date = Column(Date, nullable=False)
    outstanding_principal = Column(Float, nullable=False)
    interest_accrued = Column(Float, nullable=False, default=0.0)
    collateral_btc = Column(Float, nullable=False)
    notes = Column(Text)


class DailySnapshot(Base):
    """One row per calendar date: the portfolio totals captured when the digest
    runs, used to compute day-over-day change."""
    __tablename__ = "daily_snapshots"
    snapshot_date = Column(Date, primary_key=True)
    total_market_value = Column(Float)
    total_unrealized_pnl = Column(Float)
    created_at = Column(DateTime)


def init_db() -> None:
    Base.metadata.create_all(engine)
