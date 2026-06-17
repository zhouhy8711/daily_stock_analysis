from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.storage import DatabaseManager, StockDaily
from tools.refresh_qfq_after_corporate_actions import (
    CorporateActionEvent,
    QFQ_REFRESH_SINA_SOURCE,
    parse_corporate_action_frame,
    run_apply_from_report,
    run_refresh,
)


@pytest.fixture()
def db() -> DatabaseManager:
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url="sqlite:///:memory:")
    yield manager
    DatabaseManager.reset_instance()


def _event() -> CorporateActionEvent:
    return CorporateActionEvent(
        code="688498",
        event_date=date(2026, 5, 18),
        summary="10转4.5; 派7元",
        source="unit",
        event_hash="unit6884980518",
    )


def _daily_frame(closes: dict[str, float], *, ratio: float = 1.0) -> pd.DataFrame:
    records = []
    for index, (day, close) in enumerate(closes.items(), start=1):
        adjusted_close = round(close / ratio, 4)
        records.append(
            {
                "date": pd.Timestamp(day),
                "open": round(adjusted_close * 0.98, 4),
                "high": round(adjusted_close * 1.03, 4),
                "low": round(adjusted_close * 0.97, 4),
                "close": adjusted_close,
                "volume": 10000 + index,
                "amount": 1000000 + index,
            }
        )
    return pd.DataFrame(records)


def _history_fetcher(frame: pd.DataFrame):
    def _fetch(_code: str, start_date: date, end_date: date) -> tuple[pd.DataFrame, str]:
        filtered = frame.copy()
        parsed = pd.to_datetime(filtered["date"], errors="coerce").dt.date
        filtered = filtered.loc[(parsed >= start_date) & (parsed <= end_date)].copy()
        return filtered, QFQ_REFRESH_SINA_SOURCE

    return _fetch


def _close_for(db: DatabaseManager, code: str, day: date) -> float:
    rows = db.get_data_range(code, day, day)
    assert len(rows) == 1
    return rows[0].close


def test_parse_corporate_action_frame_normalizes_events() -> None:
    frame = pd.DataFrame(
        [
            {
                "股票代码": "688498.SH",
                "除权除息日": "2026-05-18",
                "分红方案": "10转4.5派7元",
            },
            {
                "股票代码": "000001",
                "除权除息日": "2025-05-18",
                "分红方案": "outside range",
            },
        ]
    )

    events = parse_corporate_action_frame(
        frame,
        source="unit",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    assert [event.code for event in events] == ["688498"]
    assert events[0].event_date == date(2026, 5, 18)
    assert "10转4.5派7元" in events[0].summary


def test_parse_corporate_action_frame_uses_default_code_for_detail_rows() -> None:
    frame = pd.DataFrame(
        [
            {
                "公告日期": "2026-05-12",
                "送股": 0,
                "转增": 4.5,
                "派息": 7.0,
                "除权除息日": "2026-05-18",
            },
            {
                "公告日期": "2026-03-01",
                "送股": 0,
                "转增": 0,
                "派息": 0,
                "除权除息日": pd.NaT,
            }
        ]
    )

    events = parse_corporate_action_frame(
        frame,
        source="unit-detail",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        default_code="688498",
    )

    assert len(events) == 1
    assert events[0].code == "688498"
    assert events[0].event_date == date(2026, 5, 18)
    assert "转增=4.5" in events[0].summary


def test_dry_run_detects_majority_ratio_cluster_when_some_rows_are_already_current(
    db: DatabaseManager,
    tmp_path,
) -> None:
    old_prices = {
        "2026-03-02": 800.0,
        "2026-03-03": 820.0,
        "2026-03-04": 840.0,
        "2026-03-05": 854.0,
        "2026-05-15": 1075.73,
    }
    db.save_daily_data(_daily_frame(old_prices), "688498", data_source="mixed-qfq")
    fresh = _daily_frame(old_prices, ratio=1.45)
    already_current_index = fresh.index[fresh["date"] == pd.Timestamp("2026-05-15")][0]
    for column, multiplier in {"open": 0.98, "high": 1.03, "low": 0.97, "close": 1.0}.items():
        fresh.loc[already_current_index, column] = round(old_prices["2026-05-15"] * multiplier, 4)

    report, _report_path = run_refresh(
        db,
        event_start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 5),
        events=[_event()],
        history_fetcher=_history_fetcher(fresh),
        apply=False,
        skip_chip=True,
        min_overlap=3,
        report_dir=tmp_path,
    )

    assert report["triggered_codes"] == ["688498"]
    assert report["candidates"][0]["close_ratio_consistency"] == pytest.approx(0.8)


def test_dry_run_detects_stale_qfq_without_writing_database(
    db: DatabaseManager,
    tmp_path,
) -> None:
    old_prices = {
        "2026-03-02": 800.0,
        "2026-03-03": 820.0,
        "2026-03-04": 840.0,
        "2026-03-05": 854.0,
        "2026-05-19": 600.0,
    }
    db.save_daily_data(_daily_frame(old_prices), "688498", data_source="old-qfq")
    fresh = _daily_frame(old_prices, ratio=1.45)

    report, _report_path = run_refresh(
        db,
        event_start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 5),
        events=[_event()],
        history_fetcher=_history_fetcher(fresh),
        apply=False,
        skip_chip=True,
        min_overlap=3,
        report_dir=tmp_path,
    )

    assert report["triggered_codes"] == ["688498"]
    assert report["candidate_status_counts"] == {"triggered": 1}
    assert _close_for(db, "688498", date(2026, 3, 5)) == 854.0


def test_apply_refresh_rewrites_full_local_history_from_earliest_date(
    db: DatabaseManager,
    tmp_path,
) -> None:
    old_prices = {
        "2026-03-02": 800.0,
        "2026-03-03": 820.0,
        "2026-03-04": 840.0,
        "2026-03-05": 854.0,
        "2026-05-19": 600.0,
    }
    db.save_daily_data(_daily_frame(old_prices), "688498", data_source="old-qfq")
    fresh = _daily_frame(old_prices, ratio=1.45)

    report, _report_path = run_refresh(
        db,
        event_start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 5),
        events=[_event()],
        history_fetcher=_history_fetcher(fresh),
        apply=True,
        skip_chip=True,
        min_overlap=3,
        report_dir=tmp_path,
    )

    assert report["triggered_codes"] == ["688498"]
    assert report["apply_results"][0]["status"] == "refreshed"
    assert report["apply_results"][0]["refreshed_rows"] == 5
    assert _close_for(db, "688498", date(2026, 3, 5)) == pytest.approx(round(854.0 / 1.45, 4))
    with db.get_session() as session:
        source = session.execute(
            StockDaily.__table__.select().where(
                (StockDaily.code == "688498") & (StockDaily.date == date(2026, 3, 5))
            )
        ).mappings().first()["data_source"]
    assert source == QFQ_REFRESH_SINA_SOURCE


def test_apply_from_report_reuses_dry_run_triggered_candidates(
    db: DatabaseManager,
    tmp_path,
) -> None:
    old_prices = {
        "2026-03-02": 800.0,
        "2026-03-03": 820.0,
        "2026-03-04": 840.0,
        "2026-03-05": 854.0,
    }
    db.save_daily_data(_daily_frame(old_prices), "688498", data_source="old-qfq")
    fresh = _daily_frame(old_prices, ratio=1.45)
    _report, dry_run_path = run_refresh(
        db,
        event_start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 5),
        events=[_event()],
        history_fetcher=_history_fetcher(fresh),
        apply=False,
        skip_chip=True,
        min_overlap=3,
        report_dir=tmp_path,
    )

    apply_report, _apply_path = run_apply_from_report(
        db,
        dry_run_report_path=dry_run_path,
        history_fetcher=_history_fetcher(fresh),
        skip_chip=True,
        report_dir=tmp_path,
    )

    assert apply_report["mode"] == "apply-from-report"
    assert apply_report["triggered_codes"] == ["688498"]
    assert _close_for(db, "688498", date(2026, 3, 5)) == pytest.approx(round(854.0 / 1.45, 4))


def test_apply_does_not_refresh_when_qfq_prices_are_current(
    db: DatabaseManager,
    tmp_path,
) -> None:
    prices = {
        "2026-03-02": 550.0,
        "2026-03-03": 560.0,
        "2026-03-04": 570.0,
        "2026-03-05": 580.0,
        "2026-05-19": 600.0,
    }
    current = _daily_frame(prices)
    db.save_daily_data(current, "688498", data_source="current-qfq")

    report, _report_path = run_refresh(
        db,
        event_start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 5),
        events=[_event()],
        history_fetcher=_history_fetcher(current),
        apply=True,
        skip_chip=True,
        min_overlap=3,
        report_dir=tmp_path,
    )

    assert report["triggered_codes"] == []
    assert report["apply_results"] == []
    assert _close_for(db, "688498", date(2026, 3, 5)) == 580.0
