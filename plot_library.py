import pandas as pd
import matplotlib.pyplot as plt
import plotly.graph_objects as go

def create_plot(dataframe: pd.DataFrame, column_names: list):
    plt.plot(dataframe.index, dataframe[column_names])
    plt.xlabel("Time")
    plt.ylabel("Money")
    plt.grid(visible=True)
    plt.legend(column_names)
    plt.show()

def create_and_save_plot(dataframe: pd.DataFrame, column_names: list, path_to_save_fig: str):
    plt.plot(dataframe.index, dataframe[column_names])
    plt.xlabel("Time")
    plt.ylabel("Money")
    plt.grid(visible=True)
    plt.legend(column_names)
    plt.savefig(path_to_save_fig)

# test
def create_and_save_candlestick_plot(dataframe: pd.DataFrame, column_name: str, resample_rule: str):
    resample_df=dataframe.resample(resample_rule).ffill()
    open_close_low_high=dataframe[column_name].resample(resample_rule).aggregate(['min', 'max', 'first', 'last'])

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