from __future__ import annotations

import pytest

from tools.fill_stock_daily_gaps_sina import build_parser, main


def test_fill_stock_daily_gaps_sina_defaults_to_single_worker() -> None:
    parser = build_parser()

    args = parser.parse_args(["--start-date", "2026-05-07", "--end-date", "2026-06-05"])

    assert args.parallelism == 1


def test_fill_stock_daily_gaps_sina_rejects_multiple_workers() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--start-date", "2026-05-07", "--end-date", "2026-06-05", "--parallelism", "2"])

    assert exc_info.value.code == 2
