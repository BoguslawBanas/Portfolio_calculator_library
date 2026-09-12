"""
Class-based alternative wiring plot_library.py's chart logic to a Portfolio instance (see
portfolio_calculator_library.py) — a sketch, not wired into the rest of the codebase. Unlike
the first version of this file, it does not import plot_library: each method's plotly code is
inlined here, driven by whatever DataFrame/columns the Portfolio instance already has
(computing them via Portfolio's own methods first if they aren't there yet). Built on plotly
alone (not matplotlib) so every chart type - including the candlestick - comes from one library.
"""

import pandas as pd
import plotly.graph_objects as go
from .portfolio_calculator_library import Portfolio

# Colorblind-safe categorical palette (fixed order, never cycled/generated) — same values as
# plot_library.py's, duplicated here since this file intentionally doesn't import that module.
CATEGORICAL_COLORS=['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948']
COLOR_GOOD='#0ca30c'
COLOR_CRITICAL='#d03b3b'
COLOR_OTHER='#898781'
COLOR_BASELINE='#c3c2b7'


class Plot:
    # Default argument values for the plot methods below, exposed as class constants so a
    # caller can override the library-wide default for a given argument in one place (e.g.
    # Plot.PERFORMANCE_PLOT_RESAMPLE_RULE='M') instead of passing it explicitly on every call.
    MONEY_PLOT_KIND='plot'
    PERFORMANCE_PLOT_KIND='plot'
    PERFORMANCE_PLOT_RESAMPLE_RULE='W'
    REVENUE_PLOT_INCLUDE_DIVIDENDS=True
    ALLOCATION_PLOT_BY='ticker'
    ALLOCATION_PLOT_KIND='pie'
    ALLOCATION_PLOT_METRIC='invested'
    ALLOCATION_COMPARISON_PLOT_BY='ticker'

    def __init__(self, portfolio: Portfolio):
        """portfolio: a constructed Portfolio instance (portfolio_calculator_library.py). If it
        hasn't been merged yet (self.portfolio not set), merge it now so every plot method below
        has a DataFrame to draw from."""
        self.portfolio=portfolio
        # if not hasattr(self.portfolio, 'portfolio'):
        #     self.portfolio.portfolio=Portfolio.merge(self.portfolio.dataframes)

    @staticmethod
    def _render(fig: go.Figure, path_to_save_fig: str=None):
        """Shared save-or-show tail for every chart below. Static export (write_image) requires
        the kaleido package; interactive display (show) does not."""
        if path_to_save_fig:
            fig.write_image(path_to_save_fig)
        else:
            fig.show()

    def money_plot(self, kind: str=MONEY_PLOT_KIND, path_to_save_fig: str=None):
        """Money invested vs. total revenue over time.
        kind: 'plot' — two overlaid line plots, or 'stacked_plot' — stacked area plot."""
        dataframe=self.portfolio.portfolio
        revenue=dataframe[self.portfolio.MONEY_INVESTED_COLUMN]+dataframe[self.portfolio.PROFIT_COLUMN]

        if kind=='plot':
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.MONEY_INVESTED_COLUMN], mode='lines', name='Money_invested'),
                go.Scatter(x=dataframe.index, y=revenue, mode='lines', name='Revenue'),
            ])
        elif kind=='stacked_plot':
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.MONEY_INVESTED_COLUMN], mode='lines', name='Money_invested', stackgroup='one'),
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.PROFIT_COLUMN], mode='lines', name='Profit', stackgroup='one'),
            ])
        else:
            raise ValueError(f"Unknown money_plot kind: {kind!r} (expected 'plot' or 'stacked_plot')")

        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def performance_plot(self, kind: str=PERFORMANCE_PLOT_KIND, resample_rule: str=PERFORMANCE_PLOT_RESAMPLE_RULE, path_to_save_fig: str=None):
        """Portfolio performance over time, driven by the Irr column.
        kind: 'plot' — line plot of IRR, or 'candlestick' — candlestick of IRR aggregated
        over resample_rule (min/max/first/last per bucket)."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()
        dataframe=self.portfolio.portfolio

        if kind=='plot':
            # Kept off money_plot on purpose: IRR is a percentage, and mixing it in would mean a dual-axis chart.
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.IRR_COLUMN], mode='lines', line=dict(color=CATEGORICAL_COLORS[0]))
            ])
            fig.update_layout(xaxis_title="Time", yaxis_title="IRR (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        elif kind=='candlestick':
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
            raise ValueError(f"Unknown performance_plot kind: {kind!r} (expected 'plot' or 'candlestick')")

    def revenue_plot(self, include_dividends: bool=REVENUE_PLOT_INCLUDE_DIVIDENDS, path_to_save_fig: str=None):
        """Portfolio revenue (total gain) over time — a simpler, non-IRR read of performance
        than performance_plot. Reads self.portfolio.data directly rather than
        self.portfolio.portfolio, so — unlike performance_plot/period_return_bar_plot — it needs
        no prior calculate_irr()/calculate_money_earned_between_dates_column() call.
        include_dividends: True (default) — a single 'Revenue' line, Profit as-is (dividends
        already summed into it); False — two separate lines, revenue with dividends backed out
        (Profit - Dividend) and dividends on their own. DIVIDEND_COLUMN defaults to 0 below when
        absent (no Stock/PolishRetailBonds source, the only two asset types that produce one)."""
        dataframe=self.portfolio.data
        dividends=dataframe.get(self.portfolio.DIVIDEND_COLUMN, pd.Series(0.0, index=dataframe.index))

        if include_dividends:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.PROFIT_COLUMN], mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0]))
            ])
        else:
            fig=go.Figure(data=[
                go.Scatter(x=dataframe.index, y=dataframe[self.portfolio.PROFIT_COLUMN]-dividends, mode='lines', name='Revenue', line=dict(color=CATEGORICAL_COLORS[0])),
                go.Scatter(x=dataframe.index, y=dividends, mode='lines', name='Dividends', line=dict(color=CATEGORICAL_COLORS[1])),
            ])

        fig.update_layout(xaxis_title="Time", yaxis_title="Money", legend=dict(x=0, y=1))
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    def period_return_bar_plot(self, days_between: int=0, offset: int=0, path_to_save_fig: str=None):
        if self.portfolio.DAILY_RETURN_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_money_earned_between_dates_column(days_between, offset)
        dataframe=self.portfolio.portfolio

        colors=[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in dataframe[self.portfolio.DAILY_RETURN_COLUMN]]
        fig=go.Figure(data=[
            go.Bar(x=dataframe.index, y=dataframe[self.portfolio.DAILY_RETURN_COLUMN], marker_color=colors, width=1.0*24*60*60*1000)
        ])
        fig.add_hline(y=0, line_color=COLOR_BASELINE, line_width=1)
        fig.update_layout(xaxis_title="Time", yaxis_title="Daily return")
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)

    # Suffix appended to 'distribution_by_ticker'/'distribution_by_directory' to reach the
    # Portfolio attribute backing each allocation_plot metric.
    METRIC_ATTRIBUTE_SUFFIXES={'invested': '', 'current_value': '_current_value', 'revenue': '_revenue'}

    def allocation_plot(self, by: str=ALLOCATION_PLOT_BY, kind: str=ALLOCATION_PLOT_KIND, metric: str=ALLOCATION_PLOT_METRIC, max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation breakdown.
        by: 'ticker' — self.portfolio.distribution_by_ticker(_current_value/_revenue), or
        'directory' — self.portfolio.distribution_by_directory(_current_value/_revenue).
        kind: 'pie' — donut chart, or 'histogram' — bar chart. metric='revenue' can produce a
        negative share (a losing position/source), which a pie chart can't represent
        meaningfully — prefer kind='histogram' whenever that's possible.
        metric: 'invested' — allocation by amount invested (cost basis), 'current_value' —
        allocation by what each position is actually worth today (cost basis still held plus
        unrealized gain), or 'revenue' — allocation by each position's share of total portfolio
        gains (unrealized + dividends + realized; can be negative for a losing position)."""
        if metric not in self.METRIC_ATTRIBUTE_SUFFIXES:
            raise ValueError(f"Unknown allocation_plot metric: {metric!r} (expected 'invested', 'current_value', or 'revenue')")
        suffix=self.METRIC_ATTRIBUTE_SUFFIXES[metric]

        if by=='ticker':
            distribution=getattr(self.portfolio, f'distribution_by_ticker{suffix}')
        elif by=='directory':
            distribution=getattr(self.portfolio, f'distribution_by_directory{suffix}')
        else:
            raise ValueError(f"Unknown allocation_plot by: {by!r} (expected 'ticker' or 'directory')")

        allocation=sorted(distribution.items(), key=lambda pair: pair[1], reverse=True)
        if len(allocation)>max_slices:
            other_value=sum(value for _, value in allocation[max_slices:])
            allocation=allocation[:max_slices]+[('Other', other_value)]

        labels_sorted, values_sorted=zip(*allocation)
        colors=list(CATEGORICAL_COLORS[:len(labels_sorted)])
        if labels_sorted[-1]=='Other':
            colors[-1]=COLOR_OTHER

        if kind=='pie':
            fig=go.Figure(data=[
                # sort=False: go.Pie defaults to re-sorting its own slices by value, which would
                # pull 'Other' out of last place (and away from COLOR_OTHER's slice) whenever the
                # smaller tickers it lumps together outweigh some single kept ticker - keep the
                # order already built above (largest ticker first, 'Other' always last) instead.
                go.Pie(labels=labels_sorted, values=values_sorted, hole=0.4, marker=dict(colors=colors), textinfo='label+percent', sort=False)
            ])
            self._render(fig, path_to_save_fig)
        elif kind=='histogram':
            fig=go.Figure(data=[
                go.Bar(x=labels_sorted, y=values_sorted, marker_color=colors)
            ])
            fig.update_layout(xaxis_title=by.capitalize(), yaxis_title="Allocation (%)")
            fig.update_yaxes(showgrid=True)
            self._render(fig, path_to_save_fig)
        else:
            raise ValueError(f"Unknown allocation_plot kind: {kind!r} (expected 'pie' or 'histogram')")

    def allocation_comparison_plot(self, by: str=ALLOCATION_COMPARISON_PLOT_BY, max_slices: int=7, path_to_save_fig: str=None):
        """Grouped bar chart comparing each ticker's/directory's allocation by amount invested
        (cost basis, the default allocation_plot metric) against its allocation by current
        market value — lets you see at a glance which positions have grown or shrunk relative
        to what was put in.
        by: 'ticker' — self.portfolio.distribution_by_ticker/_current_value, or 'directory' —
        self.portfolio.distribution_by_directory/_current_value."""
        if by=='ticker':
            invested, current_value=self.portfolio.distribution_by_ticker, self.portfolio.distribution_by_ticker_current_value
        elif by=='directory':
            invested, current_value=self.portfolio.distribution_by_directory, self.portfolio.distribution_by_directory_current_value
        else:
            raise ValueError(f"Unknown allocation_comparison_plot by: {by!r} (expected 'ticker' or 'directory')")

        # Sort/group by the invested metric (the "default" allocation_plot ordering), then carry
        # the same grouping over to current_value so both bars for a given label line up.
        ordered_keys=sorted(invested, key=invested.get, reverse=True)
        if len(ordered_keys)>max_slices:
            kept_keys, other_keys=ordered_keys[:max_slices], ordered_keys[max_slices:]
            labels=kept_keys+['Other']
            invested_values=[invested[key] for key in kept_keys]+[sum(invested[key] for key in other_keys)]
            current_values=[current_value[key] for key in kept_keys]+[sum(current_value[key] for key in other_keys)]
        else:
            labels=ordered_keys
            invested_values=[invested[key] for key in labels]
            current_values=[current_value[key] for key in labels]

        fig=go.Figure(data=[
            go.Bar(x=labels, y=invested_values, name='Invested', marker_color=CATEGORICAL_COLORS[0]),
            go.Bar(x=labels, y=current_values, name='Current value', marker_color=CATEGORICAL_COLORS[1]),
        ])
        fig.update_layout(xaxis_title=by.capitalize(), yaxis_title="Allocation (%)", barmode='group')
        fig.update_yaxes(showgrid=True)
        self._render(fig, path_to_save_fig)
