import numpy as np
import pandas as pd
from datetime import datetime
from .portfolio_calculator_library import merge_dataframes

def create_dataframe_and_get_data(dataframe_file: str) -> list:
    df=pd.read_csv(dataframe_file)

    interest_rate_df=pd.read_csv('interest_rate.csv')
    interest_rate_df['date']=pd.to_datetime(interest_rate_df['date'], format='%m-%Y')
    interest_rate_df=interest_rate_df.set_index('date')
    interest_rate_df=pd.DataFrame({}, index=pd.date_range(start=interest_rate_df.index.min(), end=datetime.today(), freq='D')).join(interest_rate_df).ffill()

    inflation_rate_df=pd.read_csv('inflation_rate.csv')
    inflation_rate_df['date']=pd.to_datetime(inflation_rate_df['date'], format='%m-%Y')
    inflation_rate_df=inflation_rate_df.set_index('date')
    inflation_rate_df=pd.DataFrame({}, index=pd.date_range(start=inflation_rate_df.index.min(), end=datetime.today(), freq='D')).join(inflation_rate_df).ffill()

    df['date']=pd.to_datetime(df['date'], format='%Y-%m-%d')
    df=df.set_index('date')
    data=list()
    for idx, row in df.iterrows():
        code=row['code'][0]
        if code=='R':
            data.append(variable_rate_bond(row['amount_of_units'], 100.0, interest_rate_df, row['additional_coupon'], idx, idx+pd.DateOffset(years=1)-pd.DateOffset(days=1), 19.0, row['is_swapped']))
        elif code=='D':
            data.append(variable_rate_bond(row['amount_of_units'], 100.0, interest_rate_df, row['additional_coupon'], idx, idx+pd.DateOffset(years=2)-pd.DateOffset(days=1), 19.0, row['is_swapped']))
        elif code=='T':
            data.append(fixed_rate_bond(row['amount_of_units'], 100.0, row['initial_coupon'], idx, idx+pd.DateOffset(years=3)-pd.DateOffset(days=1), 0.0, row['is_swapped']))
        elif code=='E':
            data.append(inflationary_rate_bond(row['amount_of_units'], 100.0, inflation_rate_df, row['initial_coupon'], row['additional_coupon'], idx, idx+pd.DateOffset(years=10)-pd.DateOffset(days=1), 0.0, row['is_swapped']))
    
    final_df=merge_dataframes(data)
    return [final_df]

def get_data_from_dataframe(dataframe: pd.DataFrame) -> list:
    interest_rate_df=pd.read_csv('interest_rate.csv')
    interest_rate_df['date']=pd.to_datetime(interest_rate_df['date'], format='%m-%Y')
    interest_rate_df=interest_rate_df.set_index('date')
    interest_rate_df=pd.DataFrame({}, index=pd.date_range(start=interest_rate_df.index.min(), end=datetime.today(), freq='D')).join(interest_rate_df).ffill()

    inflation_rate_df=pd.read_csv('inflation_rate.csv')
    inflation_rate_df['date']=pd.to_datetime(inflation_rate_df['date'], format='%m-%Y')
    inflation_rate_df=inflation_rate_df.set_index('date')
    inflation_rate_df=pd.DataFrame({}, index=pd.date_range(start=inflation_rate_df.index.min(), end=datetime.today(), freq='D')).join(inflation_rate_df).ffill()

    dataframe['date']=pd.to_datetime(dataframe['date'], format='%Y-%m-%d')
    dataframe=dataframe.set_index('date')
    data=list()
    for idx, row in dataframe.iterrows():
        code=row['code'][0]
        if code=='R':
            data.append(variable_rate_bond(row['amount_of_units'], 100.0, interest_rate_df, row['additional_coupon'], idx, idx+pd.DateOffset(years=1)-pd.DateOffset(days=1), 19.0, row['is_swapped']))
        elif code=='D':
            data.append(variable_rate_bond(row['amount_of_units'], 100.0, interest_rate_df, row['additional_coupon'], idx, idx+pd.DateOffset(years=2)-pd.DateOffset(days=1), 19.0, row['is_swapped']))
        elif code=='T':
            data.append(fixed_rate_bond(row['amount_of_units'], 100.0, row['initial_coupon'], idx, idx+pd.DateOffset(years=3)-pd.DateOffset(days=1), 0.0, row['is_swapped']))
        elif code=='E':
            data.append(inflationary_rate_bond(row['amount_of_units'], 100.0, inflation_rate_df, row['initial_coupon'], row['additional_coupon'], idx, idx+pd.DateOffset(years=10)-pd.DateOffset(days=1), 0.0, row['is_swapped']))
    
    final_df=merge_dataframes(data)
    return [final_df]

def fixed_rate_bond(amount_of_bonds: int, price_of_unit: float, coupon: float, start_date: datetime, end_date: datetime, tax: float, is_swapped: bool=False) -> pd.DataFrame:
    dataframe=pd.DataFrame({
        'Money_invested': amount_of_bonds*price_of_unit,
        'Profit_without_dividends': 0.0,
        'Profit': 0.0,
    }, index=pd.date_range(start=start_date, end=min(end_date, datetime.today())))

    dataframe['Days_from_beginning']=(dataframe.index-start_date).days
    dataframe['Years_from_beginning']=np.floor(dataframe['Days_from_beginning']/365)
    dataframe['Profit']=(amount_of_bonds*price_of_unit*(1+coupon/100)**(1+dataframe['Years_from_beginning'])-amount_of_bonds*100*(1+coupon/100)**(dataframe['Years_from_beginning']))/365.0*(1-tax/100)
    dataframe['Profit']=round(dataframe['Profit'].cumsum(), 2)
    dataframe['Profit_without_dividends']=dataframe['Profit']

    dataframe.drop(columns=['Days_from_beginning', 'Years_from_beginning'], inplace=True)
    return dataframe

def variable_rate_bond(amount_of_bonds: int, price_of_unit: float, interest_rate_df: pd.DataFrame, additional_coupon: float, start_date: datetime, end_date: datetime, tax: float, is_swapped: bool=False) -> pd.DataFrame:
    dataframe=pd.DataFrame({
        'Money_invested': amount_of_bonds*price_of_unit,
        'Profit_without_dividends': 0.0,
        'Profit': 0.0
    }, index=pd.date_range(start=start_date, end=min(end_date, datetime.today())))

    dataframe=dataframe.join(interest_rate_df)

    dataframe['Profit']=(amount_of_bonds*price_of_unit*(1+(dataframe['rate']+additional_coupon)/100)-(amount_of_bonds*price_of_unit))/365.0*(1-tax/100)
    dataframe['Profit']=round(dataframe['Profit'].cumsum(), 2)
    dataframe['Profit_without_dividends']=dataframe['Profit']

    dataframe.drop(columns=['rate'], inplace=True)

    if is_swapped:
        dataframe.loc[dataframe.index.max(), 'Profit']+=amount_of_bonds*0.1
    return dataframe

def inflationary_rate_bond(amount_of_bonds: int, price_of_unit: float, inflation_df: pd.DataFrame, initial_coupon: float, additional_coupon: float, start_date: datetime, end_date: datetime, tax: float, is_swapped: bool=False) -> pd.DataFrame:
    dataframe=pd.DataFrame({
        'Money_invested': amount_of_bonds*price_of_unit,
        'Profit_without_dividends': 0.0,
        'Profit': 0.0,
        'Daily_interest': 0.0
    }, index=pd.date_range(start=start_date, end=min(end_date, datetime.today())))

    dataframe.loc[start_date:start_date+pd.DateOffset(years=1), 'Rate']=initial_coupon
    dataframe.loc[start_date:start_date+pd.DateOffset(years=1), 'Daily_interest']=(amount_of_bonds*price_of_unit*initial_coupon/100)/365.0
    dataframe.loc[start_date:start_date+pd.DateOffset(years=1), 'Profit']=dataframe['Daily_interest'].cumsum()

    for i in range(9):
        tmp_end_date=min(start_date+pd.DateOffset(years=i+2), end_date)
        if tmp_end_date<end_date:
            break
        dataframe.loc[start_date+pd.DateOffset(years=i+1, days=1):tmp_end_date, 'Rate']=inflation_df.loc[pd.to_datetime(start_date+pd.DateOffset(years=i+1)-pd.DateOffset(months=1)), 'inflation']+additional_coupon
        dataframe.loc[start_date+pd.DateOffset(years=i+1):tmp_end_date, 'Daily_interest']=((amount_of_bonds*price_of_unit+dataframe.loc[start_date+pd.DateOffset(years=i+1), 'Close'])*dataframe['Rate']/100)/365.0
        dataframe.loc[start_date+pd.DateOffset(years=i+1):tmp_end_date, 'Profit']=dataframe['Daily_interest'].cumsum()
            
    dataframe.drop(columns=['Rate', 'Daily_interest'], inplace=True)
    dataframe['Profit']=round(dataframe['Profit']*(1-tax/100), 2)
    dataframe['Profit_without_dividends']=dataframe['Profit']

    if is_swapped:
        dataframe.loc[dataframe.index.max(), 'Profit']+=amount_of_bonds*0.1
    return dataframe