"""A ResourceLog in memory, for the deck's event log, crash banner and history screens."""

from cardak.core.store import DayPeak, ResourceEvent


class FakeLog:
    def __init__(
        self,
        events: list[ResourceEvent] | None = None,
        days: list[DayPeak] | None = None,
        medians: dict[str, tuple[float, float]] | None = None,
        hours: dict[str, list[tuple[int, int, float]]] | None = None,
    ) -> None:
        self.events = events or []
        self.days = days or []
        self.medians = medians or {}
        self.hours = hours or {}
        self.dismissed: list[float] = []

    def resource_events_since(self, ts: float) -> list[ResourceEvent]:
        return [e for e in self.events if e.ts >= ts]

    def latest_crash(self, since: float) -> ResourceEvent | None:
        crashes = [
            e for e in self.events
            if e.kind == "crash" and e.level == "critical" and e.ts >= since
            and e.ts not in self.dismissed
        ]  # fmt: skip
        return crashes[-1] if crashes else None

    def dismiss_report(self, ts: float) -> None:
        self.dismissed.append(ts)

    def history_days(self, since: float) -> list[DayPeak]:
        return self.days

    def history_medians(self, since: float) -> dict[str, tuple[float, float]]:
        return self.medians

    def history_hours(self, subject: str, since: float) -> list[tuple[int, int, float]]:
        return self.hours.get(subject, [])

    def alert_counts(self, since: float) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            if e.notified and e.kind not in ("crash", "daemon") and e.ts >= since:
                counts[e.subject] = counts.get(e.subject, 0) + 1
        return counts
