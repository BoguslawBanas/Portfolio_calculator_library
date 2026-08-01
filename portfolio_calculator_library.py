from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import numpy_financial as npf

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

def irr_newton(cashflows: list, guess: float, tol: float=1e-12, max_iter: int=10):
    cashflows=np.asarray(cashflows, dtype=np.float64)
    r=guess

    for _ in range(max_iter):
        t=np.arange(len(cashflows))
        denom=(1+r)**t
        f=np.sum(cashflows/denom)
        fp=np.sum(-t*cashflows/((1+r)**(t+1)))

        if abs(fp)<1e-15:
            return np.nan
        r_new=r-f/fp

        if abs(r_new-r)<tol:
            return r_new
        r=r_new
        
    return r

def calculate_irr(dataframe: pd.DataFrame, money_invested_column_name: str, revenue_column_name: str) -> pd.DataFrame:
    dataframe['Prev_money_inv']=dataframe[money_invested_column_name].shift(1).fillna(0.0)
    dataframe['Total_money']=round(dataframe[money_invested_column_name]+dataframe[revenue_column_name], 2) #test
    dataframe['Cashflow']=round(dataframe['Prev_money_inv']-dataframe[money_invested_column_name], 2)

    irr=np.full(len(dataframe['Cashflow']), np.nan)
    guess=0.1
    irr[0]=0.0

    for i in range(len(dataframe['Cashflow'])):
        guess=irr_newton(dataframe['Cashflow'].iloc[:i+1].to_list() + [dataframe['Total_money'].iloc[i]], guess=guess)
        irr[i]=round(((guess+1.0)**i-1)*100.0, 2)
        if np.isnan(guess):
            guess=0.1

    dataframe['Irr']=irr
    return dataframe.drop(columns=['Prev_money_inv', 'Cashflow'])

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

def calculate_money_earned_between_dates_column(dataframe: pd.DataFrame, days_between: int=0, offset: int=0) -> pd.DataFrame:
    dataframe['Daily_return']=0.0
    if days_between==0:
        days_between=(dataframe.index[-1]-dataframe.index[0]).days
    for idx, _ in dataframe.iterrows():
        dataframe.loc[idx, 'Daily_return']=round(calculate_money_earned_between_dates(dataframe, idx-pd.DateOffset(days=days_between+offset), idx-pd.DateOffset(days=offset))/days_between, 2)
    return dataframe