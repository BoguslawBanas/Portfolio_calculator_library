"""
Class-based alternative wiring plot_library.py's chart logic to a Portfolio instance (see
portfolio_calculator_library.py) — a sketch, not wired into the rest of the codebase. Unlike
the first version of this file, it does not import plot_library: each method's plotly code is
inlined here, driven by whatever DataFrame/columns the Portfolio instance already has
(computing them via Portfolio's own methods first if they aren't there yet). Built on plotly
alone (not matplotlib) so every chart type - including the candlestick - comes from one library.
"""

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

    @staticmethod
    def _render(fig: go.Figure, path_to_save_fig: str=None):
        """Shared save-or-show tail for every chart below. Static export (write_image) requires
        the kaleido package; interactive display (show) does not."""
        if path_to_save_fig:
            fig.write_image(path_to_save_fig)
        else:
            fig.show()

    def money_plot(self, kind: str='plot', path_to_save_fig: str=None):
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

    def performance_plot(self, kind: str='plot', resample_rule: str='W', path_to_save_fig: str=None):
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
            self._render(fig, path_to_save_fig)
        else:
            raise ValueError(f"Unknown performance_plot kind: {kind!r} (expected 'plot' or 'candlestick')")

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

    def allocation_plot(self, by: str='ticker', kind: str='pie', max_slices: int=7, path_to_save_fig: str=None):
        """Portfolio allocation breakdown.
        by: 'ticker' — self.portfolio.distribution_by_ticker, or 'directory' — self.portfolio.distribution_by_directory.
        kind: 'pie' — donut chart, or 'histogram' — bar chart."""
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
