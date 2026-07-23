import pandas as pd
import matplotlib.pyplot as plt

def create_plot(dataframe: pd.DataFrame, column_names: list):
    plt.plot(dataframe.index, dataframe[column_names])
    plt.xlabel("Time")
    plt.ylabel("Money")
    plt.grid(visible=True)
    plt.legend(column_names)
    plt.show()