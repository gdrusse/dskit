"""Pinned Yahoo chart archives; no network or synthetic settlement."""
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo

from ..base import AssetError
from .localtables import PinnedArchiveConnector

__all__ = ["YahooChartArchiveConnector"]


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
        events = doc.get("events")
        inventory = events.get("splits") if isinstance(events, dict) else None
        verified = declared and isinstance(inventory, dict)
        splits = [datetime.fromtimestamp(int(v["date"]), timezone.utc).astimezone(zone).date()
                  for v in (inventory or {}).values()] if isinstance(inventory, dict) else []
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
