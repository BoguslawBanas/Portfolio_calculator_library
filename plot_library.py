"""
Class-based alternative wiring plot_library.py's chart logic to a Portfolio instance (see
portfolio_calculator_library.py) — a sketch, not wired into the rest of the codebase. Unlike
the first version of this file, it does not import plot_library: each method's matplotlib/
plotly code is inlined here, driven by whatever DataFrame/columns the Portfolio instance
already has (computing them via Portfolio's own methods first if they aren't there yet).
"""

import matplotlib.pyplot as plt
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
    def __init__(self, portfolio: Portfolio):
        """portfolio: a constructed Portfolio instance (portfolio_calculator_library.py). If it
        hasn't been merged yet (self.portfolio not set), merge it now so every plot method below
        has a DataFrame to draw from."""
        self.portfolio=portfolio
        # if not hasattr(self.portfolio, 'portfolio'):
        #     self.portfolio.portfolio=Portfolio.merge(self.portfolio.dataframes)

    def money_plot(self, kind: str='plot', path_to_save_fig: str=None):
        """Money invested vs. total revenue over time.
        kind: 'plot' — two overlaid line plots, or 'stacked_plot' — stacked area plot."""
        dataframe=self.portfolio.portfolio

        if kind=='plot':
            plt.plot(dataframe.index, dataframe[self.portfolio.MONEY_INVESTED_COLUMN])
            plt.plot(dataframe.index, dataframe[self.portfolio.MONEY_INVESTED_COLUMN]+dataframe[self.portfolio.PROFIT_COLUMN])
        elif kind=='stacked_plot':
            plt.stackplot(dataframe.index, dataframe[self.portfolio.MONEY_INVESTED_COLUMN], dataframe[self.portfolio.PROFIT_COLUMN])
        else:
            raise ValueError(f"Unknown money_plot kind: {kind!r} (expected 'plot' or 'stacked_plot')")

        plt.xlabel("Time")
        plt.ylabel("Money")
        plt.grid(visible=True)
        plt.legend(['Money_invested', 'Revenue'], loc='upper left')
        if path_to_save_fig:
            plt.savefig(path_to_save_fig)
        else:
            plt.show()

    def performance_plot(self, kind: str='plot', resample_rule: str='W', path_to_save_fig: str=None):
        """Portfolio performance over time, driven by the Irr column.
        kind: 'plot' — matplotlib line plot of IRR, or 'candlestick' — plotly candlestick
        of IRR aggregated over resample_rule (min/max/first/last per bucket)."""
        if self.portfolio.IRR_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_irr()
        dataframe=self.portfolio.portfolio

        if kind=='plot':
            # Kept off money_plot on purpose: IRR is a percentage, and mixing it in would mean a dual-axis chart.
            plt.plot(dataframe.index, dataframe[self.portfolio.IRR_COLUMN], color=CATEGORICAL_COLORS[0])
            plt.xlabel("Time")
            plt.ylabel("IRR (%)")
            plt.grid(visible=True)
            if path_to_save_fig:
                plt.savefig(path_to_save_fig)
            else:
                plt.show()
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
            fig.show()
        else:
            raise ValueError(f"Unknown performance_plot kind: {kind!r} (expected 'plot' or 'candlestick')")

    def period_return_bar_plot(self, days_between: int=0, offset: int=0, path_to_save_fig: str=None):
        if self.portfolio.DAILY_RETURN_COLUMN not in self.portfolio.portfolio.columns:
            self.portfolio.calculate_money_earned_between_dates_column(days_between, offset)
        dataframe=self.portfolio.portfolio

        colors=[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in dataframe[self.portfolio.DAILY_RETURN_COLUMN]]
        plt.bar(dataframe.index, dataframe[self.portfolio.DAILY_RETURN_COLUMN], color=colors, width=1.0)
        plt.axhline(0, color=COLOR_BASELINE, linewidth=1)
        plt.xlabel("Time")
        plt.ylabel("Daily return")
        plt.grid(visible=True, axis='y')
        if path_to_save_fig:
            plt.savefig(path_to_save_fig)
        else:
            plt.show()

    def allocation_plot(self, by: str='ticker', kind: str='pie', max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation breakdown.
        by: 'ticker' — self.portfolio.distribution_by_ticker, or 'directory' — self.portfolio.distribution_by_directory.
        kind: 'pie' — plotly donut chart, or 'histogram' — matplotlib bar chart."""
        if by=='ticker':
            distribution=self.portfolio.distribution_by_ticker
        elif by=='directory':
            distribution=self.portfolio.distribution_by_directory
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
                go.Pie(labels=labels_sorted, values=values_sorted, hole=0.4, marker=dict(colors=colors), textinfo='label+percent')
            ])
            fig.show()
        elif kind=='histogram':
            plt.bar(labels_sorted, values_sorted, color=colors)
            plt.xlabel(by.capitalize())
            plt.ylabel("Allocation (%)")
            plt.grid(visible=True, axis='y')
            if path_to_save_fig:
                plt.savefig(path_to_save_fig)
            else:
                plt.show()
        else:
            raise ValueError(f"Unknown allocation_plot kind: {kind!r} (expected 'pie' or 'histogram')")
