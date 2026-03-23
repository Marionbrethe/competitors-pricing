from datetime import datetime, timezone
from pydantic import BaseModel, field_validator


class PriceRecord(BaseModel):
    company: str       # "Bounce" | "Stasher" | "Radical Storage"
    city: str
    location_name: str
    address: str
    size: str          # "small" | "regular" | "odd-sized" | "flat-rate"
    price: float
    currency: str      # e.g. "GBP", "USD", "EUR"
    price_unit: str    # e.g. "day"
    scraped_at: datetime

    @field_validator("size")
    @classmethod
    def normalise_size(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("currency")
    @classmethod
    def normalise_currency(cls, v: str) -> str:
        return v.strip().upper()

    @classmethod
    def now(cls) -> datetime:
        return datetime.now(timezone.utc)
