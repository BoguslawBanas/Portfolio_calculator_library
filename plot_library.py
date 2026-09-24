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
    ALLOCATION_PLOT_KIND_PIE='pie'
    ALLOCATION_PLOT_KIND_HISTOGRAM='histogram'
    ALLOCATION_PLOT_METRIC_INVESTED='invested'
    ALLOCATION_PLOT_METRIC_CURRENT_VALUE='current_value'
    ALLOCATION_PLOT_METRIC_REVENUE='revenue'

    ALLOCATION_COMPARISON_PLOT_BY_TICKER='ticker'
    ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY='directory'

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

    def money_plot(self, kind: str=MONEY_PLOT_KIND_PLOT, path_to_save_fig: str=None):
        """Money invested vs. total revenue over time.
        kind: 'plot' — two overlaid line plots, or 'stacked_plot' — stacked area plot."""
        dataframe=self.portfolio.portfolio
        # MONEY_INVESTED_COLUMN/PROFIT_COLUMN are Decimal - cast to float for plotly, which
        # doesn't render Decimal values.
        money_invested=dataframe[self.portfolio.MONEY_INVESTED_COLUMN].astype(float)
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        revenue=money_invested+profit

        if kind==self.MONEY_PLOT_KIND_PLOT:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=money_invested, mode='lines', name='Money_invested'),
                go.Scatter(x=dataframe.index, y=revenue, mode='lines', name='Revenue'),
            ])
        elif kind==self.MONEY_PLOT_KIND_STACKED_PLOT:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=money_invested, mode='lines', name='Money_invested', stackgroup='one'),
                go.Scatter(x=dataframe.index, y=profit, mode='lines', name='Profit', stackgroup='one'),
            ])
        else:
            raise ValueError(f"Unknown money_plot kind: {kind!r} (expected {self.MONEY_PLOT_KIND_PLOT!r} or {self.MONEY_PLOT_KIND_STACKED_PLOT!r})")

        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def performance_plot(self, kind: str=PERFORMANCE_PLOT_KIND_PLOT, resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE_WEEKLY, path_to_save_fig: str=None):
        """Portfolio performance over time, driven by the Irr column.
        kind: 'plot' — line plot of IRR, or 'candlestick' — candlestick of IRR aggregated
        over resample_rule (min/max/first/last per bucket)."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()
        dataframe=self.portfolio.portfolio

        if kind==self.PERFORMANCE_PLOT_KIND_PLOT:
            # Kept off money_plot on purpose: IRR is a percentage, and mixing it in would mean a dual-axis chart.
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.IRR_COLUMN], mode='lines', line=dict(color=CATEGORICAL_COLORS[0]))
            ])
            fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        elif kind==self.PERFORMANCE_PLOT_KIND_CANDLESTICK:
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
        performance_plot."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()
        # unlike self.portfolio, benchmark may have no .portfolio yet at all - hasattr guards
        # that first-ever call instead of raising.
        if not hasattr(benchmark, 'portfolio') or benchmark.IRR_COLUMN not in benchmark.portfolio.columns:
            benchmark.calculate_irr()

        portfolio_irr=self.portfolio.portfolio[self.portfolio.IRR_COLUMN].resample(resample_rule).ffill()
        benchmark_irr=benchmark.portfolio[benchmark.IRR_COLUMN].resample(resample_rule).ffill()

        fig=go.Figure(data=[
            go.Scatter(x=portfolio_irr.index, y=portfolio_irr, mode='lines', name='Portfolio', line=dict(color=CATEGORICAL_COLORS[0])),
            go.Scatter(x=benchmark_irr.index, y=benchmark_irr, mode='lines', name=benchmark_name, line=dict(color=CATEGORICAL_COLORS[1])),
        ])
        fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def revenue_plot(self, include_dividends: bool=True, path_to_save_fig: str=None):
        """Portfolio revenue (total gain) over time - a simpler, non-IRR read of performance.
        Reads self.portfolio.data, not .portfolio, so unlike performance_plot/
        period_return_bar_plot it needs no prior calculate_irr()/
        calculate_money_earned_between_dates_column() call.
        include_dividends: True - single 'Revenue' line (Profit as-is); False - two lines,
        dividends backed out of revenue and shown separately. DIVIDEND_COLUMN defaults to 0 if
        absent (no Stock source)."""
        dataframe=self.portfolio.data
        # PROFIT_COLUMN/DIVIDEND_COLUMN are Decimal - cast to float for plotly, which doesn't
        # render Decimal values. dividends' absent-column fallback is float too, matching that.
        profit=dataframe[self.portfolio.PROFIT_COLUMN].astype(float)
        dividends=dataframe[self.portfolio.DIVIDEND_COLUMN].astype(float) if self.portfolio.DIVIDEND_COLUMN in dataframe else pd.Series(0.0, index=dataframe.index)

        if include_dividends:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=profit, mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0]))
            ])
        else:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=profit-dividends, mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0])),
                go.Scatter(x=dataframe.index, y=dividends, mode='lines', name='Dividends', line=dict(color=CATEGORICAL_COLORS[1])),
            ])

        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
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

    # Suffix appended to 'distribution_by_ticker'/'distribution_by_directory' to reach the
    # backing Portfolio attribute for each allocation_plot metric.
    METRIC_ATTRIBUTE_SUFFIXES={ALLOCATION_PLOT_METRIC_INVESTED: '', ALLOCATION_PLOT_METRIC_CURRENT_VALUE: '_current_value', ALLOCATION_PLOT_METRIC_REVENUE: '_revenue'}

    def allocation_plot(self, by: str=ALLOCATION_PLOT_BY_TICKER, kind: str=ALLOCATION_PLOT_KIND_PIE, metric: str=ALLOCATION_PLOT_METRIC_INVESTED, max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation breakdown.
        by: 'ticker' or 'directory' - self.portfolio.distribution_by_ticker/_directory
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
        else:
            raise ValueError(f"Unknown allocation_plot by: {by!r} (expected {self.ALLOCATION_PLOT_BY_TICKER!r} or {self.ALLOCATION_PLOT_BY_DIRECTORY!r})")

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
