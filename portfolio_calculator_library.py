from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import numpy_financial as npf
from joblib import Parallel, delayed

def create_dataframe_and_get_data(dataframe_file: str, isin_column_name: str) -> list:
    df=pd.read_csv(dataframe_file)

    dataframes=dict()
    for _, row in df.iterrows():
        if dataframes.get(row[isin_column_name]) is None:
            dataframes[row[isin_column_name]]=pd.DataFrame()
        dataframes[row[isin_column_name]]=pd.concat([dataframes[row[isin_column_name]], row], axis=1)

    list_of_dataframes=list(dataframes.values())
    for i in range(len(list_of_dataframes)):
        list_of_dataframes[i]=list_of_dataframes[i].transpose()
        list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i]['date'], format='%Y-%m-%d') #test
        list_of_dataframes[i].drop(columns=['date'], inplace=True)

    return list_of_dataframes

def get_data_from_dataframe(dataframe: pd.DataFrame, isin_column_name: str) -> list:
    dataframes=dict()
    for _, row in dataframe.iterrows():
        if dataframes.get(row[isin_column_name]) is None:
            dataframes[row[isin_column_name]]=pd.DataFrame()
        dataframes[row[isin_column_name]]=pd.concat([dataframes[row[isin_column_name]], row], axis=1)

    list_of_dataframes=list(dataframes.values())
    for i in range(len(list_of_dataframes)):
        list_of_dataframes[i]=list_of_dataframes[i].transpose()
        list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i]['date'], format='%Y-%m-%d') #test
        list_of_dataframes[i].drop(columns=['date'], inplace=True)

    return list_of_dataframes

def calculate_irr(dataframe: pd.DataFrame, money_invested_column_name: str, revenue_column_name: str) -> pd.DataFrame:
    dataframe['Prev_money_inv']=dataframe[money_invested_column_name].shift(1).fillna(0.0)
    dataframe['Total_money']=round(dataframe[money_invested_column_name]+dataframe[revenue_column_name], 2) #test
    dataframe['Cashflow']=round(dataframe['Prev_money_inv']-dataframe[money_invested_column_name], 2)

    dataframe['Irr']=0.0
    for i, _ in dataframe.iterrows():
        print(i)
        dataframe.loc[i, 'Irr']=npf.irr(dataframe['Cashflow'][:i].to_list() + [dataframe['Total_money'][i]])

    # irr=np.empty(len(dataframe['Cashflow'])+1)
    # irr_cashflow_arr=np.empty(2)
    # irr=np.empty(len(dataframe['Cashflow']))

    # for i in range(len(dataframe['Cashflow'])):
    #     print(i)
    #     irr_cashflow_arr=np.resize(irr_cashflow_arr, i+2)
    #     irr_cashflow_arr[i]=dataframe['Cashflow'].iloc[i]
    #     irr_cashflow_arr[i+1]=dataframe['Total_money'].iloc[i]
    #     irr[i]=npf.irr(irr_cashflow_arr)
    # dataframe['Irr']=irr

    dataframe['Irr']=dataframe['Irr']+1.0
    dataframe['Irr']=[round((v**i-1)*100, 2) for i, v in enumerate(dataframe['Irr'])]

    dataframe.drop(columns=['Prev_money_inv', 'Cashflow'], inplace=True)
    return dataframe

def resample_dataframe(dataframe: pd.DataFrame, resample_rule: str) -> pd.DataFrame:
    timedelta_to_subtract: pd.DateOffset
    if resample_rule[0]=='D':
        timedelta_to_subtract=pd.DateOffset(days=1)
    elif resample_rule[0]=='W':
        timedelta_to_subtract=pd.DateOffset(weeks=1)
    elif resample_rule[0]=='M':
        timedelta_to_subtract=pd.DateOffset(months=1)
    elif resample_rule[0]=='Q':
        timedelta_to_subtract=pd.DateOffset(months=3)
    elif resample_rule[0]=='Y':
        timedelta_to_subtract=pd.DateOffset(years=1)

    new_row=pd.DataFrame(
        [{col: 0.0 for col in dataframe.columns}],
        index=[pd.to_datetime(dataframe.index[0]-timedelta_to_subtract, format='%Y-%m-%d')]
    )
    dataframe=pd.concat([dataframe, new_row]).sort_index()
    return dataframe.resample(resample_rule).ffill()

def merge_dataframes(dataframes: list) -> pd.DataFrame:
    return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

def get_earliest_date(dataframes: list) -> datetime:
    earliest_date=datetime.today()
    for df in dataframes:
        if df.index.min()<earliest_date:
            earliest_date=df.index.min()
    return earliest_date

def calculate_money_earned_between_dates(dataframe: pd.DataFrame, start_date: datetime, end_date: datetime) -> float:
    start_date_profit=0.0
    end_date_profit=0.0

    if start_date.strftime('%Y-%m-%d') in dataframe.index:
        start_date_profit=dataframe.loc[start_date.strftime('%Y-%m-%d'), 'Profit']

    if end_date.strftime('%Y-%m-%d') in dataframe.index:
        end_date_profit=dataframe.loc[end_date.strftime('%Y-%m-%d'), 'Profit']

    return end_date_profit-start_date_profit

def calculate_money_earned_between_dates_column(dataframe: pd.DataFrame, days_between: int, offset: int=0) -> pd.DataFrame:
    dataframe['Daily_return']=0.0
    for idx, _ in dataframe.iterrows():
        dataframe.loc[idx, 'Daily_return']=round(calculate_money_earned_between_dates(dataframe, idx-pd.DateOffset(days=days_between+offset), idx-pd.DateOffset(days=offset))/days_between, 2)
    return dataframe