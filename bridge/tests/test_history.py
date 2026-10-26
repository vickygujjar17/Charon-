"""Tests for :mod:`charon_bridge.history` -- the closed-trade arithmetic.

These run without MetaTrader5 installed, which is the point of keeping the
statistics out of :mod:`charon_bridge.mt5_adapter`. A wrong profit factor is
a wrong decision, so the numbers are checked here against hand-computed
deals rather than against a live account.
"""

from __future__ import annotations

from typing import Any

import pytest

from charon_bridge import history


def deal(**overrides: Any) -> dict[str, Any]:
    """One synthetic MT5 deal, in the package's own field names."""
    base: dict[str, Any] = {
        "ticket": 1,
        "position_id": 1,
        "symbol": "EURUSD",
        "type": 0,
        "entry": 0,
        "volume": 1.0,
        "price": 1.10000,
        "profit": 0.0,
        "swap": 0.0,
        "commission": 0.0,
        "time": 1_760_000_000,
        "magic": 7,
        "comment": "",
    }
    base.update(overrides)
    return base


def round_trip(
    position_id: int = 1,
    *,
    opened: int = 1_760_000_000,
    closed: int = 1_760_003_600,
    profit: float = 100.0,
    entry_price: float = 1.10000,
    exit_price: float = 1.10100,
    commission: float = 0.0,
    swap: float = 0.0,
    symbol: str = "EURUSD",
) -> list[dict[str, Any]]:
    """The two deals MT5 writes for one closed long position."""
    return [
        deal(
            ticket=position_id * 10,
            position_id=position_id,
            symbol=symbol,
            type=0,
            entry=0,
            price=entry_price,
            commission=commission / 2,
            time=opened,
        ),
        deal(
            ticket=position_id * 10 + 1,
            position_id=position_id,
            symbol=symbol,
            # --- an OUT deal *sells*, so a long closes with type 1
            type=1,
            entry=1,
            price=exit_price,
            profit=profit,
            swap=swap,
            commission=commission / 2,
            time=closed,
        ),
    ]


class TestPairDeals:
    def test_pairs_one_round_trip_into_one_trade(self) -> None:
        trades = history.pair_deals(round_trip())
        assert len(trades) == 1
        assert trades[0].position_id == 1
        assert trades[0].entry_price == pytest.approx(1.10000)
        assert trades[0].exit_price == pytest.approx(1.10100)

    def test_a_closing_sell_means_the_trade_was_a_buy(self) -> None:
        """Side is the opposite of the closing deal's type, not equal to it."""
        assert history.pair_deals(round_trip())[0].side == "BUY"

    def test_a_closing_buy_means_the_trade_was_a_sell(self) -> None:
        deals = round_trip()
        deals[1]["type"] = 0
        assert history.pair_deals(deals)[0].side == "SELL"

    def test_realized_folds_profit_swap_and_both_commissions(self) -> None:
        trade = history.pair_deals(round_trip(profit=100.0, commission=-4.0, swap=-1.0))[0]
        assert trade.commission == pytest.approx(-4.0)
        assert trade.realized == pytest.approx(95.0)

    def test_an_unclosed_position_is_not_a_trade(self) -> None:
        """The open leg belongs to the live snapshot, not to realized history."""
        opened_only = [deal(position_id=9, entry=0)]
        assert history.pair_deals(opened_only) == []

    def test_an_out_deal_without_its_in_leg_still_pairs(self) -> None:
        """History windows can clip the opening deal; the close is still real."""
        trades = history.pair_deals([deal(position_id=4, entry=1, type=1, profit=12.0)])
        assert len(trades) == 1
        assert trades[0].realized == pytest.approx(12.0)

    def test_partial_closes_are_summed_into_one_trade(self) -> None:
        """Two OUT legs on one position are one trade with combined money."""
        deals = [
            deal(position_id=5, entry=0, time=1_760_000_000),
            deal(position_id=5, entry=1, type=1, profit=10.0, time=1_760_000_100),
            deal(position_id=5, entry=1, type=1, profit=15.0, time=1_760_000_900),
        ]
        trades = history.pair_deals(deals)
        assert len(trades) == 1
        assert trades[0].realized == pytest.approx(25.0)
        # --- the last close is when the position actually finished
        assert trades[0].closed_at == 1_760_000_900

    def test_deals_without_a_position_id_are_ignored(self) -> None:
        assert history.pair_deals([deal(position_id=0, entry=1, profit=50.0)]) == []

    def test_symbol_filter_keeps_only_that_instrument(self) -> None:
        deals = round_trip(1, symbol="EURUSD") + round_trip(2, symbol="GBPUSD")
        assert [t.symbol for t in history.pair_deals(deals, "GBPUSD")] == ["GBPUSD"]

    def test_trades_are_sorted_by_closing_time(self) -> None:
        deals = round_trip(2, closed=1_760_009_000) + round_trip(1, closed=1_760_001_000)
        assert [t.position_id for t in history.pair_deals(deals)] == [1, 2]

    def test_none_input_is_empty_not_an_error(self) -> None:
        assert history.pair_deals(None) == []  # type: ignore[arg-type]

    def test_object_deals_are_accepted_as_well_as_dicts(self) -> None:
        """The adapter hands over dataclasses; the API hands over dicts."""

        class Row:
            position_id = 3
            entry = 1
            type = 1
            symbol = "EURUSD"
            price = 1.2
            volume = 0.5
            profit = 7.0
            swap = 0.0
            commission = 0.0
            time = 1_760_005_000
            comment = "tp"

        trades = history.pair_deals([Row()])
        assert len(trades) == 1
        assert trades[0].realized == pytest.approx(7.0)
        assert trades[0].comment == "tp"


class TestMetrics:
    def test_no_trades_yields_unknown_ratios_not_zero(self) -> None:
        """'No trades yet' and 'you lost everything' must not look alike."""
        stats = history.metrics([])
        assert stats["total"] == 0
        assert stats["win_rate"] is None
        assert stats["profit_factor"] is None
        assert stats["avg_rr"] is None
        assert stats["expectancy"] is None
        assert stats["best"] is None
        assert stats["worst"] is None

    def test_win_and_loss_counts_split_flats_out(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=50.0)
            + round_trip(2, profit=-20.0)
            + round_trip(3, profit=0.0)
        )
        stats = history.metrics(trades)
        assert (stats["total"], stats["wins"], stats["losses"], stats["flat"]) == (
            3,
            1,
            1,
            1,
        )

    def test_win_rate_is_wins_over_all_closed_trades(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=50.0) + round_trip(2, profit=-20.0)
        )
        assert history.metrics(trades)["win_rate"] == pytest.approx(0.5)

    def test_profit_factor_is_gross_profit_over_gross_loss(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=150.0) + round_trip(2, profit=-50.0)
        )
        stats = history.metrics(trades)
        assert stats["gross_profit"] == pytest.approx(150.0)
        assert stats["gross_loss"] == pytest.approx(50.0)
        assert stats["profit_factor"] == pytest.approx(3.0)

    def test_profit_factor_is_none_when_nothing_lost(self) -> None:
        """Undefined, not 999 and not infinity -- both would read as perfect."""
        trades = history.pair_deals(round_trip(profit=10.0))
        assert history.metrics(trades)["profit_factor"] is None

    def test_avg_rr_is_none_without_a_losing_trade_to_dividide_by(self) -> None:
        trades = history.pair_deals(round_trip(profit=10.0))
        assert history.metrics(trades)["avg_rr"] is None

    def test_avg_rr_compares_average_win_to_average_loss(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=300.0) + round_trip(2, profit=-100.0)
        )
        stats = history.metrics(trades)
        assert stats["avg_win"] == pytest.approx(300.0)
        assert stats["avg_loss"] == pytest.approx(100.0)
        assert stats["avg_rr"] == pytest.approx(3.0)

    def test_expectancy_is_mean_result_per_trade(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=100.0) + round_trip(2, profit=-40.0)
        )
        assert history.metrics(trades)["expectancy"] == pytest.approx(30.0)

    def test_net_realized_is_the_sum_of_every_trade(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=100.0)
            + round_trip(2, profit=-40.0)
            + round_trip(3, profit=-60.0)
        )
        assert history.metrics(trades)["net_realized"] == pytest.approx(0.0)

    def test_commission_alone_can_turn_a_win_into_a_loss(self) -> None:
        """A gross +5 with -8 in costs is a losing trade, not a break-even."""
        deal_rows = round_trip(profit=5.0, commission=-8.0)
        assert history.metrics(history.pair_deals(deal_rows))["losses"] == 1

    def test_streaks_count_consecutive_runs_chronologically(self) -> None:
        rows: list[dict[str, Any]] = []
        # --- W W L L L W: best win streak 2, best loss streak 3
        for index, pnl in enumerate([10.0, 10.0, -5.0, -5.0, -5.0, 10.0]):
            rows += round_trip(index + 1, profit=pnl, closed=1_760_000_000 + index * 60)
        stats = history.metrics(history.pair_deals(rows))
        assert stats["max_win_streak"] == 2
        assert stats["max_loss_streak"] == 3

    def test_a_flat_trade_breaks_a_streak_without_extending_it(self) -> None:
        rows: list[dict[str, Any]] = []
        for index, pnl in enumerate([10.0, 0.0, 10.0]):
            rows += round_trip(index + 1, profit=pnl, closed=1_760_000_000 + index * 60)
        stats = history.metrics(history.pair_deals(rows))
        assert stats["max_win_streak"] == 1

    def test_best_and_worst_are_extremes(self) -> None:
        trades = history.pair_deals(
            round_trip(1, profit=90.0) + round_trip(2, profit=-70.0)
        )
        stats = history.metrics(trades)
        assert stats["best"] == pytest.approx(90.0)
        assert stats["worst"] == pytest.approx(-70.0)

    def test_avg_hold_ignores_zero_duration_trades(self) -> None:
        rows = round_trip(1, opened=1_760_000_000, closed=1_760_000_600)
        rows += round_trip(2, opened=1_760_001_000, closed=1_760_001_000)
        assert history.metrics(history.pair_deals(rows))["avg_hold_seconds"] == 600


class TestSessions:
    def test_every_session_is_named(self) -> None:
        assert [name for name, _, _ in history.SESSIONS] == [
            "ASIAN",
            "LONDON",
            "NEW_YORK",
        ]

    def test_boundaries_are_half_open(self) -> None:
        """07:59 is still Asian; 08:00 is already London."""
        def at(hour: int, minute: int = 0) -> int:
            # --- 2025-01-01T00:00:00Z as a base, offset by hand
            return 1_735_689_600 + hour * 3600 + minute * 60

        assert history.session_of(at(7, 59)) == "ASIAN"
        assert history.session_of(at(8, 0)) == "LONDON"
        assert history.session_of(at(12, 59)) == "LONDON"
        assert history.session_of(at(13, 0)) == "NEW_YORK"
        assert history.session_of(at(21, 59)) == "NEW_YORK"

    def test_the_late_gap_falls_into_the_following_asian_session(self) -> None:
        """22:00-24:00 is before the Asian open, not a 'closed' market."""
        assert history.session_of(1_735_689_600 + 23 * 3600) == "ASIAN"

    def test_the_offset_shifts_the_bucket(self) -> None:
        """A broker on UTC+3 reaches London three hours 'earlier' in UTC."""
        base = 1_735_689_600 + 5 * 3600  # --- 05:00 UTC
        assert history.session_of(base) == "ASIAN"
        assert history.session_of(base, offset_minutes=180) == "LONDON"

    def test_by_session_always_lists_all_three(self) -> None:
        rows = history.by_session([])
        assert [row["session"] for row in rows] == ["ASIAN", "LONDON", "NEW_YORK"]
        for row in rows:
            assert row["total"] == 0
            assert row["win_rate"] is None

    def test_by_session_buckets_by_closing_time(self) -> None:
        # --- 09:00 UTC closes in London
        trades = history.pair_deals(
            round_trip(closed=1_735_689_600 + 9 * 3600, profit=25.0)
        )
        rows = {row["session"]: row for row in history.by_session(trades)}
        assert rows["LONDON"]["total"] == 1
        assert rows["LONDON"]["net_realized"] == pytest.approx(25.0)
        assert rows["ASIAN"]["total"] == 0


class TestEquityCurve:
    def test_empty_history_yields_a_single_origin_point(self) -> None:
        points = history.equity_curve([])
        assert len(points) == 1
        assert points[0]["cumulative"] == 0.0

    def test_curve_starts_at_zero_and_accumulates(self) -> None:
        """It tracks the strategy's result, so it begins at 0, not at balance."""
        trades = history.pair_deals(
            round_trip(1, profit=100.0, closed=1_760_000_100)
            + round_trip(2, profit=-30.0, closed=1_760_000_200)
        )
        points = history.equity_curve(trades)
        assert [p["cumulative"] for p in points] == [0.0, 100.0, 70.0]

    def test_points_are_in_closing_order_regardless_of_input(self) -> None:
        trades = history.pair_deals(
            round_trip(2, profit=50.0, closed=1_760_000_900)
            + round_trip(1, profit=10.0, closed=1_760_000_100)
        )
        assert [p["position_id"] for p in history.equity_curve(trades)] == [0, 1, 2]


class TestReport:
    def test_report_carries_every_section_the_dashboard_reads(self) -> None:
        payload = history.report(round_trip(profit=40.0))
        assert set(payload) == {
            "trades",
            "metrics",
            "sessions",
            "equity",
            "tz_offset_minutes",
            "session_windows",
        }

    def test_report_trades_are_newest_first(self) -> None:
        deals = round_trip(1, closed=1_760_000_100) + round_trip(2, closed=1_760_000_900)
        payload = history.report(deals)
        assert [row["position_id"] for row in payload["trades"]] == [2, 1]

    def test_limit_truncates_the_table_but_not_the_metrics(self) -> None:
        """A summary that changed when you paged the table would be useless."""
        rows: list[dict[str, Any]] = []
        for index in range(5):
            rows += round_trip(index + 1, profit=10.0, closed=1_760_000_000 + index * 60)
        payload = history.report(rows, limit=2)
        assert len(payload["trades"]) == 2
        assert payload["metrics"]["total"] == 5

    def test_report_rows_carry_session_and_outcome(self) -> None:
        payload = history.report(round_trip(profit=5.0))
        row = payload["trades"][0]
        assert row["outcome"] == "win"
        assert row["session"] in {"ASIAN", "LONDON", "NEW_YORK"}

    def test_report_echoes_the_clock_it_used(self) -> None:
        """The server timezone is not discoverable, so it is stated."""
        payload = history.report([], offset_minutes=-240)
        assert payload["tz_offset_minutes"] == -240
