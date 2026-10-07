"""Pinned Yahoo chart archives; no network or synthetic settlement."""
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo

from ..base import AssetError
from .localtables import PinnedArchiveConnector

__all__ = ["YahooChartArchiveConnector", "YahooSplitInventory"]



class YahooSplitInventory:
    """Validated split events, distinct from a caller's completeness assertion.

    Missing/null inventory is unknown; an explicit empty object is present.
    This parsed-object validator cannot detect JSON member names already lost
    during decoding and does not certify prices, dividends or source vintages.
    """

    def __init__(self, result, zone):
        from datetime import tzinfo

        zone = ZoneInfo(zone) if isinstance(zone, str) else zone
        if not isinstance(zone, tzinfo) or not isinstance(result, dict):
            raise AssetError(["split inventory requires chart object and session timezone"])
        events = result.get("events")
        if events is not None and not isinstance(events, dict):
            raise AssetError(["split inventory events must be an object"])
        inventory = None if events is None else events.get("splits")
        if inventory is not None and not isinstance(inventory, dict):
            raise AssetError(["split inventory must be an object"])
        self.present = inventory is not None
        self.ratios = {}
        for event in (inventory or {}).values():
            try:
                if not isinstance(event, dict) or type(event.get("date")) is not int:
                    raise ValueError("invalid effective timestamp")
                day = datetime.fromtimestamp(event["date"], zone).date().isoformat()
                values = [event.get("numerator"), event.get("denominator")]
                if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
                    raise ValueError("invalid split factor")
                ratio = values[0] / values[1]
                if not math.isfinite(ratio) or ratio <= 0:
                    raise ValueError("invalid split ratio")
                if day in self.ratios:
                    raise ValueError("duplicate effective date")
            except (ValueError, TypeError, OverflowError, OSError) as error:
                raise AssetError(["invalid split inventory: " + str(error)]) from error
            self.ratios[day] = ratio

    def require_bar_dates(self, dates):
        """Require present inventory and one emitted bar for every action date."""
        from collections import Counter

        if not self.present:
            raise AssetError(["split inventory is unknown"])
        counts = Counter(dates)
        if any(counts[day] != 1 for day in self.ratios):
            raise AssetError(["split inventory needs exactly one emitted bar per event date"])


class YahooChartArchiveConnector(PinnedArchiveConnector):
    """Read daily price history with explicit completion and split provenance."""

    STREAM_KEYS = {"prices": ("symbol", "quote_date")}
    EXTRA_PARAMS = ("complete_through", "corporate_actions_complete")

    def normalize(self, stream, raw):
        """Yield price observations from a verified chart response.

        Parameters
        ----------
        stream : str
            Declared prices stream.
        raw : bytes
            Verified chart JSON.

        Yields
        ------
        dict
            Daily close, separate adjusted close, and split-unit guard.
        """
        complete = date.fromisoformat(self._archive_config["complete_through"])
        doc = self.decode(raw)["chart"]["result"][0]
        stamps = doc["timestamp"]
        quote = doc["indicators"]["quote"][0]
        adjusted = doc["indicators"]["adjclose"][0]["adjclose"]
        if any(len(v) != len(stamps) for v in [*quote.values(), adjusted]):
            raise AssetError(["Yahoo chart arrays are not aligned"])
        zone = ZoneInfo(self._archive_config["session_timezone"])
        declared = self._archive_config["corporate_actions_complete"]
        if type(declared) is not bool:
            raise AssetError(["corporate_actions_complete must be boolean"])
        inventory = YahooSplitInventory(doc, zone)
        verified = declared and inventory.present
        splits = [date.fromisoformat(day) for day in inventory.ratios]
        for i, stamp in enumerate(stamps):
            instant = datetime.fromtimestamp(stamp, timezone.utc)
            day = instant.astimezone(zone).date()
            row = {"symbol": doc["meta"]["symbol"], "quote_date": day.isoformat(),
                   "effective_at": instant.isoformat(), "source_timestamp": instant.isoformat(),
                   "price_basis": "split_adjusted", "close_complete": day <= complete,
                   "unit_history_verified": verified,
                   "post_session_split": any(s > day for s in splits) if verified else None}
            for key in ("open", "high", "low", "close", "volume"):
                value = quote[key][i]
                row[key] = value if value is not None and math.isfinite(value) else None
            value = adjusted[i]
            row["adjusted_close"] = value if value is not None and math.isfinite(value) else None
            yield row
