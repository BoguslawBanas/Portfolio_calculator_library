"""
Charting layer wiring plotly to a Portfolio instance: each method is driven by whatever
DataFrame/columns the Portfolio already has, computing them via Portfolio's own methods first if
missing. Plotly only (not matplotlib), so every chart type - including candlestick - is one
library.
"""

import pandas as pd
import plotly.graph_objects as go
from .portfolio_calculator_library import Portfolio

# Colorblind-safe categorical palette (fixed order, never cycled/generated).
CATEGORICAL_COLORS=['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948']
COLOR_GOOD='#0ca30c'
COLOR_CRITICAL='#d03b3b'
COLOR_OTHER='#898781'
COLOR_BASELINE='#c3c2b7'


class Plot:
    # Allowed values for each plot method's enumerated args, exposed as constants so a caller
    # can refer to one without retyping the literal (e.g. kind=Plot.ALLOCATION_PLOT_KIND_HISTOGRAM).
    MONEY_PLOT_KIND_PLOT='plot'
    MONEY_PLOT_KIND_STACKED_PLOT='stacked_plot'

    PERFORMANCE_PLOT_KIND_PLOT='plot'
    PERFORMANCE_PLOT_KIND_CANDLESTICK='candlestick'
    # Pandas resample offset aliases ('ME'/'QE'/'YE', not 'M'/'Q'/'Y' - deprecated since pandas 2.2).
    PERFORMANCE_PLOT_RESAMPLE_RULE_DAILY='D'
    PERFORMANCE_PLOT_RESAMPLE_RULE_WEEKLY='W'
    PERFORMANCE_PLOT_RESAMPLE_RULE_MONTHLY='ME'
    PERFORMANCE_PLOT_RESAMPLE_RULE_QUARTERLY='QE'
    PERFORMANCE_PLOT_RESAMPLE_RULE_YEARLY='YE'

    ALLOCATION_PLOT_BY_TICKER='ticker'
    ALLOCATION_PLOT_BY_DIRECTORY='directory'
    ALLOCATION_PLOT_BY_CURRENCY='currency'
    ALLOCATION_PLOT_KIND_PIE='pie'
    ALLOCATION_PLOT_KIND_HISTOGRAM='histogram'
    ALLOCATION_PLOT_METRIC_INVESTED='invested'
    ALLOCATION_PLOT_METRIC_CURRENT_VALUE='current_value'
    ALLOCATION_PLOT_METRIC_REVENUE='revenue'

    ALLOCATION_COMPARISON_PLOT_BY_TICKER='ticker'
    ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY='directory'

    ALLOCATION_OVER_TIME_PLOT_KIND_PLOT='plot'
    ALLOCATION_OVER_TIME_PLOT_KIND_STACKED_PLOT='stacked_plot'

    def __init__(self, portfolio: Portfolio):
        """portfolio: a constructed Portfolio that's already had calculate_irr()/resample()/
        calculate_money_earned_between_dates_column() called at least once - whichever's needed
        first, since each sets portfolio.portfolio (the DataFrame money_plot/performance_plot/
        period_return_bar_plot read from) as a side effect."""
        self.portfolio=portfolio

    @staticmethod
    def _render(fig: go.Figure, path_to_save_fig: str=None):
        """Shared save-or-show tail for every chart below. Static export (write_image) requires
        the kaleido package; interactive display (show) does not."""
        if path_to_save_fig:
            fig.write_image(path_to_save_fig)
        else:
            fig.show()

    @staticmethod
    def _reindex_benchmark_column(benchmark, column: str, index) -> pd.Series:
        """One Benchmark column (Decimal - cast to float), reindexed (ffill) onto index, for a
        direct overlay against the portfolio's own same-shaped series - used by money_plot's/
        revenue_plot's own benchmark= overlay. Benchmark's own .data is always present from
        construction, no calculate_irr()/... prerequisite (unlike its .portfolio, see
        _benchmark_irr below)."""
        return benchmark.data[column].astype(float).reindex(index).ffill()

    def _benchmark_irr(self, benchmark, resample_rule: str) -> pd.Series:
        """Benchmark's own IRR, resampled (ffill) to resample_rule - runs calculate_irr() first
        if it hasn't yet (unlike self.portfolio, a Benchmark may have no .portfolio at all on a
        first-ever call, so hasattr guards that instead of raising). Shared by
        benchmark_comparison_plot and performance_plot's own benchmark= overlay."""
        if not hasattr(benchmark, 'portfolio') or benchmark.IRR_COLUMN not in benchmark.portfolio.columns:
            benchmark.calculate_irr()
        return benchmark.portfolio[benchmark.IRR_COLUMN].resample(resample_rule).ffill()

    def money_plot(self, kind: str=MONEY_PLOT_KIND_PLOT, benchmark=None, benchmark_name: str='Benchmark', path_to_save_fig: str=None):
        """Money invested vs. total revenue over time.
        kind: 'plot' — two overlaid line plots, or 'stacked_plot' — stacked area plot.
        benchmark: optional Benchmark (from Portfolio.simulate_benchmark) to overlay - its own
        revenue (Money_invested + Profit), reindexed (ffill) onto this portfolio's own dates, as
        an extra line regardless of kind - so a benchmark trailing in IRR% but ahead in absolute
        money (or vice versa, see performance_plot's own IRR-only comparison) is visible directly.
        benchmark_name: legend label for the benchmark's line, used only when benchmark is given."""
        dataframe=self.portfolio.portfolio
        # MONEY_INVESTED_COLUMN/PROFIT_COLUMN are Decimal - cast to float for plotly, which
        # doesn't render Decimal values.
        money_invested=dataframe[self.portfolio.MONEY_INVESTED_COLUMN].astype(float)
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        revenue=money_invested+profit

        if kind==self.MONEY_PLOT_KIND_PLOT:
            traces=[
                go.Scatter(x=dataframe.index, y=money_invested, mode='lines', name='Money_invested'),
                go.Scatter(x=dataframe.index, y=revenue, mode='lines', name='Revenue'),
            ]
        elif kind==self.MONEY_PLOT_KIND_STACKED_PLOT:
            traces=[
                go.Scatter(x=dataframe.index, y=money_invested, mode='lines', name='Money_invested', stackgroup='one'),
                go.Scatter(x=dataframe.index, y=profit, mode='lines', name='Profit', stackgroup='one'),
            ]
        else:
            raise ValueError(f"Unknown money_plot kind: {kind!r} (expected {self.MONEY_PLOT_KIND_PLOT!r} or {self.MONEY_PLOT_KIND_STACKED_PLOT!r})")

        if benchmark is not None:
            benchmark_money_invested=self._reindex_benchmark_column(benchmark, benchmark.MONEY_INVESTED_COLUMN, dataframe.index)
            benchmark_profit=self._reindex_benchmark_column(benchmark, benchmark.PROFIT_COLUMN, dataframe.index)
            traces.append(go.Scatter(x=dataframe.index, y=benchmark_money_invested+benchmark_profit, mode='lines', name=f'{benchmark_name} revenue'))

        fig=go.Figure(data=traces)
        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def performance_plot(self, kind: str=PERFORMANCE_PLOT_KIND_PLOT, resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE_WEEKLY, benchmark=None, benchmark_name: str='Benchmark', path_to_save_fig: str=None):
        """Portfolio performance over time, driven by the Irr column.
        kind: 'plot' — line plot of IRR, or 'candlestick' — candlestick of IRR aggregated
        over resample_rule (min/max/first/last per bucket).
        benchmark: optional Benchmark (from Portfolio.simulate_benchmark) to overlay against
        kind='plot' - both IRR series resampled (ffill) to resample_rule, same as
        benchmark_comparison_plot (which this supersedes for kind='plot'). Not supported for
        kind='candlestick' - a second series doesn't overlay cleanly on a candlestick.
        benchmark_name: legend label for the benchmark's line, used only when benchmark is given."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()
        dataframe=self.portfolio.portfolio

        if kind==self.PERFORMANCE_PLOT_KIND_PLOT:
            if benchmark is not None:
                # Kept off money_plot on purpose: IRR is a percentage, and mixing it in would mean a dual-axis chart.
                portfolio_irr=dataframe[self.portfolio.IRR_COLUMN].resample(resample_rule).ffill()
                benchmark_irr=self._benchmark_irr(benchmark, resample_rule)
                fig=go.Figure(data=[
                    go.Scatter(x=portfolio_irr.index, y=portfolio_irr, mode='lines', name='Portfolio', line=dict(color=CATEGORICAL_COLORS[0])),
                    go.Scatter(x=benchmark_irr.index, y=benchmark_irr, mode='lines', name=benchmark_name, line=dict(color=CATEGORICAL_COLORS[1])),
                ])
                fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)", legend=dict(x=0, y=1))
            else:
                fig=go.Figure(data=[
                    go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.IRR_COLUMN], mode='lines', line=dict(color=CATEGORICAL_COLORS[0]))
                ])
                fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        elif kind==self.PERFORMANCE_PLOT_KIND_CANDLESTICK:
            if benchmark is not None:
                raise ValueError(f"performance_plot's benchmark overlay isn't supported for kind={kind!r} (only {self.PERFORMANCE_PLOT_KIND_PLOT!r}).")
            resample_df=dataframe.resample(resample_rule).ffill()
            open_close_low_high=dataframe[self.portfolio.IRR_COLUMN].resample(resample_rule).aggregate(['min', 'max', 'first', 'last'])

            fig=go.Figure(data=[
                go.Candlestick(
                    x=resample_df.index,
                    open=open_close_low_high['first'],
                    close=open_close_low_high['last'],
                    low=open_close_low_high['min'],
                    high=open_close_low_high['max']
                )
            ])
            fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        else:
            raise ValueError(f"Unknown performance_plot kind: {kind!r} (expected {self.PERFORMANCE_PLOT_KIND_PLOT!r} or {self.PERFORMANCE_PLOT_KIND_CANDLESTICK!r})")

    def benchmark_comparison_plot(self, benchmark, benchmark_name: str='Benchmark', resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE_WEEKLY, path_to_save_fig: str=None):
        """Overlays this portfolio's own IRR against a Benchmark's - same cash-flow timing,
        different asset - so the gap between the lines is the portfolio's edge (or lag) over
        putting the same money into the benchmark instead.
        benchmark: a Benchmark from self.portfolio.simulate_benchmark(ticker, ...).
        benchmark_name: legend label for the benchmark's line.
        resample_rule: both IRR series are resampled (ffill) to this rule, same as
        performance_plot. Equivalent to performance_plot(kind='plot', benchmark=benchmark, ...)."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()

        portfolio_irr=self.portfolio.portfolio[self.portfolio.IRR_COLUMN].resample(resample_rule).ffill()
        benchmark_irr=self._benchmark_irr(benchmark, resample_rule)

        fig=go.Figure(data=[
            go.Scatter(x=portfolio_irr.index, y=portfolio_irr, mode='lines', name='Portfolio', line=dict(color=CATEGORICAL_COLORS[0])),
            go.Scatter(x=benchmark_irr.index, y=benchmark_irr, mode='lines', name=benchmark_name, line=dict(color=CATEGORICAL_COLORS[1])),
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def revenue_plot(self, include_dividends: bool=True, benchmark=None, benchmark_name: str='Benchmark', path_to_save_fig: str=None):
        """Portfolio revenue (total gain) over time - a simpler, non-IRR read of performance.
        Reads self.portfolio.data, not .portfolio, so unlike performance_plot/
        period_return_bar_plot it needs no prior calculate_irr()/
        calculate_money_earned_between_dates_column() call.
        include_dividends: True - single 'Revenue' line (Profit as-is); False - two lines,
        dividends backed out of revenue and shown separately. DIVIDEND_COLUMN defaults to 0 if
        absent (no Stock source).
        benchmark: optional Benchmark (from Portfolio.simulate_benchmark) to overlay - its own
        Profit, reindexed (ffill) onto this portfolio's own dates, as an extra line.
        benchmark_name: legend label for the benchmark's line, used only when benchmark is given."""
        dataframe=self.portfolio.data
        # PROFIT_COLUMN/DIVIDEND_COLUMN are Decimal - cast to float for plotly, which doesn't
        # render Decimal values. dividends' absent-column fallback is float too, matching that.
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        dividends=dataframe[self.portfolio.DIVIDEND_COLUMN].astype(float) if self.portfolio.DIVIDEND_COLUMN in dataframe else pd.Series(0.0, index=dataframe.index)

        if include_dividends:
            traces=[go.Scatter(x=dataframe.index, y=profit, mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0]))]
        else:
            traces=[
                go.Scatter(x=dataframe.index, y=profit-dividends, mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0])),
                go.Scatter(x=dataframe.index, y=dividends, mode='lines', name='Dividends', line=dict(color=CATEGORICAL_COLORS[1])),
            ]

        if benchmark is not None:
            benchmark_profit=self._reindex_benchmark_column(benchmark, benchmark.PROFIT_COLUMN, dataframe.index)
            traces.append(go.Scatter(x=dataframe.index, y=benchmark_profit, mode='lines', name=f'{benchmark_name} revenue', line=dict(color=CATEGORICAL_COLORS[2])))

        fig=go.Figure(data=traces)
        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def drawdown_plot(self, path_to_save_fig: str=None):
        """Portfolio total value's (Money_invested + Profit) running peak-to-trough decline over
        time, as a percentage off its own running all-time high - a risk view money_plot/
        revenue_plot don't show. Reads self.portfolio.data, not .portfolio, so - like
        revenue_plot - it needs no prior calculate_irr()/resample()/
        calculate_money_earned_between_dates_column() call."""
        dataframe=self.portfolio.data
        # MONEY_INVESTED_COLUMN/PROFIT_COLUMN are Decimal - cast to float for plotly, which
        # doesn't render Decimal values.
        total_value=dataframe[self.portfolio.MONEY_INVESTED_COLUMN].astype(float)+dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        running_max=total_value.cummax()
        # Guarded like Portfolio's own percentage fields - a never-funded/all-zero stretch would
        # otherwise divide by 0.
        drawdown=pd.Series(0.0, index=dataframe.index)
        held=running_max>0
        drawdown[held]=(total_value[held]-running_max[held])/running_max[held]*100.0

        fig=go.Figure(data=[
            go.Scatter(x=dataframe.index, y=drawdown, mode='lines', fill='tozeroy', line=dict(color=COLOR_CRITICAL))
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="Drawdown (%)")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def cashflow_plot(self, resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE_MONTHLY, path_to_save_fig: str=None):
        """Net contributions (positive bars) and withdrawals (negative bars) per resample_rule
        period - Money_invested's day-over-day diffs, summed per bucket - complementing
        money_plot's cumulative view with how much actually moved in/out each period. Reads
        self.portfolio.data, not .portfolio, so - like revenue_plot - it needs no prior
        calculate_irr()/resample()/calculate_money_earned_between_dates_column() call."""
        dataframe=self.portfolio.data
        # MONEY_INVESTED_COLUMN is Decimal - cast to float for plotly, which doesn't render
        # Decimal values.
        money_invested=dataframe[self.portfolio.MONEY_INVESTED_COLUMN].astype(float)
        daily_cashflow=money_invested.diff().fillna(0.0)
        periodic_cashflow=daily_cashflow.resample(resample_rule).sum()

        colors=[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in periodic_cashflow]
        fig=go.Figure(data=[
            go.Bar(x=periodic_cashflow.index, y=periodic_cashflow, marker_color=colors)
        ])
        fig.add_hline(y=0, line_color=COLOR_BASELINE, line_width=1)
        fig.update_layout(xaxis_title="Time", yaxis_title="Net cashflow")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def realized_vs_unrealized_profit_plot(self, path_to_save_fig: str=None):
        """Profit split into its realized and unrealized components, as a stacked area summing
        back to Profit. Realized profit is Profit minus Profit_without_realized - the latter
        (unrealized gain on positions still held, plus dividends collected along the way,
        excluding gain/loss already locked in by a sell) is always present, unlike Dividend.
        Reads self.portfolio.data, not .portfolio, so - like revenue_plot - it needs no prior
        calculate_irr()/resample()/calculate_money_earned_between_dates_column() call."""
        dataframe=self.portfolio.data
        # PROFIT_COLUMN/PROFIT_WITHOUT_REALIZED_COLUMN are Decimal - cast to float for plotly,
        # which doesn't render Decimal values.
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        unrealized=dataframe[self.portfolio.PROFIT_WITHOUT_REALIZED_COLUMN].astype(float)
        realized=profit-unrealized

        fig=go.Figure(data=[
            go.Scatter(x=dataframe.index, y=realized, mode='lines', name='Realized profit', stackgroup='one', line=dict(color=CATEGORICAL_COLORS[0])),
            go.Scatter(x=dataframe.index, y=unrealized, mode='lines', name='Unrealized profit (incl. dividends)', stackgroup='one', line=dict(color=CATEGORICAL_COLORS[1])),
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def dividend_income_plot(self, resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE_MONTHLY, path_to_save_fig: str=None):
        """Dividends actually received per resample_rule period, as bars - Dividend's cumulative
        series diffed and summed per bucket - unlike revenue_plot's cumulative dividend line.
        DIVIDEND_COLUMN defaults to 0 if absent (no Stock source). By period only, not by ticker -
        DIVIDEND_COLUMN is already summed across every ticker by the time it reaches
        self.portfolio.data, and Portfolio doesn't separately retain a per-ticker daily series.
        Reads self.portfolio.data, not .portfolio, so - like revenue_plot - it needs no prior
        calculate_irr()/resample()/calculate_money_earned_between_dates_column() call."""
        dataframe=self.portfolio.data
        # DIVIDEND_COLUMN is Decimal - cast to float for plotly, which doesn't render Decimal
        # values.
        dividends=dataframe[self.portfolio.DIVIDEND_COLUMN].astype(float) if self.portfolio.DIVIDEND_COLUMN in dataframe else pd.Series(0.0, index=dataframe.index)
        periodic_dividends=dividends.diff().fillna(0.0).resample(resample_rule).sum()

        fig=go.Figure(data=[
            go.Bar(x=periodic_dividends.index, y=periodic_dividends, marker_color=CATEGORICAL_COLORS[0])
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="Dividend income")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def period_return_bar_plot(self, days_between: int=0, offset: int=0, path_to_save_fig: str=None):
        if self.portfolio.DAILY_RETURN_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_money_earned_between_dates_column(days_between, offset)
        dataframe=self.portfolio.portfolio
        # DAILY_RETURN_COLUMN is Decimal - cast to float for plotly, which doesn't render Decimal
        # values.
        daily_return=dataframe[self.portfolio.DAILY_RETURN_COLUMN].astype(float)

        colors=[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in daily_return]
        fig=go.Figure(data=[
            go.Bar(x=dataframe.index, y=daily_return, marker_color=colors, width=1.0*24*60*60*1000)
        ])
        fig.add_hline(y=0, line_color=COLOR_BASELINE, line_width=1)
        fig.update_layout(xaxis_title="Time", yaxis_title="Daily return")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def rolling_return_plot(self, days_between: int=90, path_to_save_fig: str=None):
        """Rolling annualized return (%) over a trailing days_between-day window, as a line -
        extends calculate_money_earned_between_dates_column's own single-window formula (the same
        shift-based windowing) into an annualized percentage of capital deployed, rather than one
        raw dollar-per-day figure (period_return_bar_plot) or one cumulative rate since inception
        (performance_plot's IRR). Each day's value is that window's profit gained, divided by
        money invested at the window's start, scaled to a year (x365/days_between).
        Reads self.portfolio.data, not .portfolio, so - unlike period_return_bar_plot - it needs
        no prior calculate_money_earned_between_dates_column() call."""
        if days_between<=0:
            raise ValueError(f"days_between must be a positive number of days, got {days_between!r}.")

        dataframe=self.portfolio.data
        # MONEY_INVESTED_COLUMN/PROFIT_COLUMN are Decimal - cast to float for plotly, which
        # doesn't render Decimal values.
        money_invested=dataframe[self.portfolio.MONEY_INVESTED_COLUMN].astype(float)
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)

        # Shifting N rows matches N calendar days on this continuous daily index (same assumption
        # calculate_money_earned_between_dates_column's own shift() relies on).
        older_profit=profit.shift(days_between).fillna(0.0)
        older_money_invested=money_invested.shift(days_between).fillna(0.0)
        window_gain=profit-older_profit

        # Guarded like Portfolio's own percentage fields - a window starting before any money was
        # invested would otherwise divide by 0.
        rolling_return=pd.Series(0.0, index=dataframe.index)
        funded=older_money_invested>0
        rolling_return[funded]=(window_gain[funded]/older_money_invested[funded])*(365.0/days_between)*100.0

        fig=go.Figure(data=[
            go.Scatter(x=dataframe.index, y=rolling_return, mode='lines', line=dict(color=CATEGORICAL_COLORS[0]))
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title=f"Rolling {days_between}-day annualized return (%)")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    # Suffix appended to 'distribution_by_ticker'/'distribution_by_directory' to reach the
    # backing Portfolio attribute for each allocation_plot metric.
    METRIC_ATTRIBUTE_SUFFIXES={ALLOCATION_PLOT_METRIC_INVESTED: '', ALLOCATION_PLOT_METRIC_CURRENT_VALUE: '_current_value', ALLOCATION_PLOT_METRIC_REVENUE: '_revenue'}

    def allocation_plot(self, by: str=ALLOCATION_PLOT_BY_TICKER, kind: str=ALLOCATION_PLOT_KIND_PIE, metric: str=ALLOCATION_PLOT_METRIC_INVESTED, max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation breakdown.
        by: 'ticker', 'directory', or 'currency' (each position's own native currency - FX
        exposure, e.g. how much of the portfolio is actually USD- vs. EUR-denominated,
        independent of currency_to) - self.portfolio.distribution_by_ticker/_directory/_currency
        (_current_value/_revenue).
        kind: 'pie' (donut) or 'histogram' (bar). metric='revenue' can go negative, which a pie
        can't represent - prefer 'histogram' there.
        metric: 'invested' (cost basis), 'current_value' (cost basis + unrealized gain), or
        'revenue' (share of total gains, can be negative)."""
        if metric not in self.METRIC_ATTRIBUTE_SUFFIXES:
            raise ValueError(f"Unknown allocation_plot metric: {metric!r} (expected {self.ALLOCATION_PLOT_METRIC_INVESTED!r}, {self.ALLOCATION_PLOT_METRIC_CURRENT_VALUE!r}, or {self.ALLOCATION_PLOT_METRIC_REVENUE!r})")
        suffix=self.METRIC_ATTRIBUTE_SUFFIXES[metric]

        if by==self.ALLOCATION_PLOT_BY_TICKER:
            distribution=getattr(self.portfolio, f'distribution_by_ticker{suffix}')
        elif by==self.ALLOCATION_PLOT_BY_DIRECTORY:
            distribution=getattr(self.portfolio, f'distribution_by_directory{suffix}')
        elif by==self.ALLOCATION_PLOT_BY_CURRENCY:
            distribution=getattr(self.portfolio, f'distribution_by_currency{suffix}')
        else:
            raise ValueError(f"Unknown allocation_plot by: {by!r} (expected {self.ALLOCATION_PLOT_BY_TICKER!r}, {self.ALLOCATION_PLOT_BY_DIRECTORY!r}, or {self.ALLOCATION_PLOT_BY_CURRENCY!r})")

        allocation=sorted(distribution.items(), key=lambda pair: pair[1], reverse=True)
        if len(allocation)>max_slices:
            other_value=sum(value for _, value in allocation[max_slices:])
            allocation=allocation[:max_slices]+[('Other', other_value)]

        labels_sorted, values_sorted=zip(*allocation)
        colors=list(CATEGORICAL_COLORS[:len(labels_sorted)])
        if labels_sorted[-1]=='Other':
            colors[-1]=COLOR_OTHER

        if kind==self.ALLOCATION_PLOT_KIND_PIE:
            fig=go.Figure(data=[
                # sort=False: go.Pie re-sorts by value by default, which could pull 'Other' out
                # of last place (and away from COLOR_OTHER) - keep the order built above instead.
                go.Pie(labels=labels_sorted, values=values_sorted, hole=0.4, marker=dict(colors=colors), textinfo='label+percent', sort=False)
            ])
            self._render(fig, path_to_save_fig)
        elif kind==self.ALLOCATION_PLOT_KIND_HISTOGRAM:
            if metric==self.ALLOCATION_PLOT_METRIC_REVENUE:
                # Revenue can go negative - flag losing positions by sign (same convention as
                # period_return_bar_plot) instead of the categorical per-slice palette above.
                colors=[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in values_sorted]
            fig=go.Figure(data=[
                go.Bar(x=labels_sorted, y=values_sorted, marker_color=colors)
            ])
            if metric==self.ALLOCATION_PLOT_METRIC_REVENUE:
                fig.add_hline(y=0, line_color=COLOR_BASELINE, line_width=1)
            fig.update_layout(xaxis_title=by.capitalize(), yaxis_title="Allocation (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        else:
            raise ValueError(f"Unknown allocation_plot kind: {kind!r} (expected {self.ALLOCATION_PLOT_KIND_PIE!r} or {self.ALLOCATION_PLOT_KIND_HISTOGRAM!r})")

    def allocation_comparison_plot(self, by: str=ALLOCATION_COMPARISON_PLOT_BY_TICKER, max_slices: int=7, path_to_save_fig: str=None):
        """Grouped bar chart: allocation by amount invested vs. by total value (Money_invested +
        Profit - still-held cost basis plus every gain ever made, realized included), per
        ticker/directory - shows at a glance which positions grew/shrunk relative to cost basis.
        by: 'ticker' or 'directory' - self.portfolio.distribution_by_ticker/_directory
        (/_total_value)."""
        if by==self.ALLOCATION_COMPARISON_PLOT_BY_TICKER:
            invested, total_value=self.portfolio.distribution_by_ticker, self.portfolio.distribution_by_ticker_total_value
        elif by==self.ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY:
            invested, total_value=self.portfolio.distribution_by_directory, self.portfolio.distribution_by_directory_total_value
        else:
            raise ValueError(f"Unknown allocation_comparison_plot by: {by!r} (expected {self.ALLOCATION_COMPARISON_PLOT_BY_TICKER!r} or {self.ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY!r})")

        # Sort by the invested metric, then carry the same grouping to total_value so both
        # bars per label line up.
        ordered_keys=sorted(invested, key=invested.get, reverse=True)
        if len(ordered_keys)>max_slices:
            kept_keys, other_keys=ordered_keys[:max_slices], ordered_keys[max_slices:]
            labels=kept_keys+['Other']
            invested_values=[invested[key] for key in kept_keys]+[sum(invested[key] for key in other_keys)]
            total_values=[total_value[key] for key in kept_keys]+[sum(total_value[key] for key in other_keys)]
        else:
            labels=ordered_keys
            invested_values=[invested[key] for key in labels]
            total_values=[total_value[key] for key in labels]

        fig=go.Figure(data=[
            go.Bar(x=labels, y=invested_values, name='Invested', marker_color=CATEGORICAL_COLORS[0]),
            go.Bar(x=labels, y=total_values, name='Total value', marker_color=CATEGORICAL_COLORS[1]),
        ])
        fig.update_layout(xaxis_title=by.capitalize(), yaxis_title="Allocation (%)", barmode='group')
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def allocation_over_time_plot(self, kind: str=ALLOCATION_OVER_TIME_PLOT_KIND_PLOT, metric: str=ALLOCATION_PLOT_METRIC_INVESTED, max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation by source directory, evolving over time - unlike allocation_plot's
        current snapshot-only pie/bar, this shows how the mix has drifted.
        By directory only, not by ticker: self.portfolio.sources_by_directory keeps each
        constructed source's own daily DataFrame, but Portfolio doesn't retain a per-ticker/
        symbol/account daily series - only each one's final lifetime totals
        (distribution_by_ticker).
        kind: 'plot' - one line per directory (each its own %, sharing the 0 baseline) - reads
        individual trends precisely, but doesn't visually guarantee they sum to 100%. Or
        'stacked_plot' - a stacked area (each day's bands sum to 100%), more intuitive for
        composition but a middle band's own trend is distorted by whatever's stacked below it.
        metric: 'invested' (cost basis, Money_invested) or 'current_value' (cost basis plus
        unrealized gain) - 'revenue' isn't offered here since it can go negative, which neither
        kind represents well (same reasoning as allocation_plot's own metric='revenue' guidance)."""
        if metric not in (self.ALLOCATION_PLOT_METRIC_INVESTED, self.ALLOCATION_PLOT_METRIC_CURRENT_VALUE):
            raise ValueError(f"Unknown allocation_over_time_plot metric: {metric!r} (expected {self.ALLOCATION_PLOT_METRIC_INVESTED!r} or {self.ALLOCATION_PLOT_METRIC_CURRENT_VALUE!r})")
        if kind not in (self.ALLOCATION_OVER_TIME_PLOT_KIND_PLOT, self.ALLOCATION_OVER_TIME_PLOT_KIND_STACKED_PLOT):
            raise ValueError(f"Unknown allocation_over_time_plot kind: {kind!r} (expected {self.ALLOCATION_OVER_TIME_PLOT_KIND_PLOT!r} or {self.ALLOCATION_OVER_TIME_PLOT_KIND_STACKED_PLOT!r})")

        index=self.portfolio.data.index
        per_directory=dict()
        for dir, source in self.portfolio.sources_by_directory.items():
            # MONEY_INVESTED_COLUMN/PROFIT_WITHOUT_DIVIDEND_COLUMN are Decimal - cast to float for
            # plotly, which doesn't render Decimal values.
            money_invested=source.data[source.MONEY_INVESTED_COLUMN].astype(float)
            if metric==self.ALLOCATION_PLOT_METRIC_CURRENT_VALUE:
                value=money_invested+source.data[source.PROFIT_WITHOUT_DIVIDEND_COLUMN].astype(float)
            else:
                value=money_invested
            # A source's own DataFrame only starts on its own first transaction date, not
            # Portfolio's overall earliest - reindexing onto the full index and filling with 0
            # (not yet funded) before it starts, ffill for any gap past its own last day.
            per_directory[dir]=value.reindex(index).ffill().fillna(0.0)

        totals=sum(per_directory.values())
        # Guarded like Portfolio's own percentage fields - a never-funded stretch would otherwise
        # divide by 0.
        percentages={dir: (series/totals*100.0).where(totals>0, 0.0) for dir, series in per_directory.items()}

        ordered_dirs=sorted(percentages, key=lambda dir: percentages[dir].iloc[-1], reverse=True)
        if len(ordered_dirs)>max_slices:
            kept_dirs, other_dirs=ordered_dirs[:max_slices], ordered_dirs[max_slices:]
            other_series=sum(percentages[dir] for dir in other_dirs)
            traces=[(dir, percentages[dir]) for dir in kept_dirs]+[('Other', other_series)]
        else:
            traces=[(dir, percentages[dir]) for dir in ordered_dirs]

        colors=list(CATEGORICAL_COLORS[:len(traces)])
        if traces[-1][0]=='Other':
            colors[-1]=COLOR_OTHER

        stackgroup='one' if kind==self.ALLOCATION_OVER_TIME_PLOT_KIND_STACKED_PLOT else None
        fig=go.Figure(data=[
            go.Scatter(x=index, y=series, mode='lines', name=str(name), stackgroup=stackgroup, line=dict(color=color))
            for (name, series), color in zip(traces, colors)
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="Allocation (%)", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)
