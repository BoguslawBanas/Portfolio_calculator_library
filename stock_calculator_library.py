import yfinance as yf
import pandas as pd 
from datetime import datetime
import json

def get_currency_exchange_rate_data(currency_from: str, cuurency_to: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    if currency_from.upper()==cuurency_to.upper():
        data=pd.DataFrame({
            'Close': 1.0
        }, index=pd.date_range(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), freq='D'))
    else:
        data=yf.download(currency_from.upper()+cuurency_to.upper()+"=X", start=start_date, end=end_date)
        data=data.droplevel(level='Ticker', axis=1)
        data.drop(columns=['High', 'Low', 'Open', 'Volume'], inplace=True)
        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=end_date, freq='D')
        )
        data=all_days.join(data).ffill().bfill()
    return data

def tranform_dataframe_to_dataframe_with_isin(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> pd.DataFrame:
    with open(path_to_json_file, 'r') as f:
        j=json.load(f)
        for i,row in dataframe.iterrows():
            dataframe.loc[i, isin_column_name]=j[row[isin_column_name]]["ticker"]
    return dataframe

def get_data_from_isin(df: pd.DataFrame, currency_from: str, currency_to: str) -> pd.DataFrame:
    start_date=df.index[0]
    end_date=datetime.today().strftime('%Y-%m-%d')
    currency_data=get_currency_exchange_rate_data(currency_from, currency_to, start_date, end_date)

    return get_data_from_isin_with_currency_data(df, currency_data)

def get_data_from_isin_with_currency_data(df: pd.DataFrame, currency_data: pd.DataFrame) -> pd.DataFrame:
    start_date=df.index[0]
    end_date=datetime.today().strftime('%Y-%m-%d')

    data=yf.download(df['isin'].iloc[0], start=start_date, end=end_date)
    data=data.droplevel(level='Ticker', axis=1)
    data.drop(columns=['High', 'Low', 'Open', 'Volume'], inplace=True)

    all_days=pd.DataFrame(
        index=pd.date_range(start=data.index.min(), end=datetime.today(), freq='D')
    )
    data=all_days.join(data).ffill()

    data['Close']=data['Close']*currency_data['Close']

    data['Money_invested']=0.0
    data['Money_invested_after_penalty']=0.0
    data['Avg_price']=0.0
    data['Dividend']=0.0
    
    for idx, rows in df.iterrows():
        if rows['state']=='buy':
            data.loc[idx, 'Money_invested']=round(rows['money_invested'], 2)
            data.loc[idx, 'Money_invested_after_penalty']=round(rows['money_invested']*(1-rows['penalty']), 2)
            data.loc[idx, 'Avg_price']=(rows['money_invested']*rows['price_of_unit']*currency_data.loc[idx, 'Close'])
        elif rows['state']=='dividend':
            data.loc[idx, 'Dividend']+=round(rows['money_invested'], 2)
        elif rows['state']=='dividend_tax':
            data.loc[idx, 'Dividend']-=round(rows['money_invested'], 2)

    data['Money_invested']=data['Money_invested'].cumsum()
    data['Money_invested_after_penalty']=data['Money_invested_after_penalty'].cumsum()
    data['Avg_price']=data['Avg_price'].cumsum()/data['Money_invested']
    data['Dividend']=data['Dividend'].cumsum()

    #check later if that is correct
    data['Profit']=round((data['Close']-data['Avg_price'])/data['Avg_price']*data['Money_invested']-data['Money_invested']+data['Money_invested_after_penalty']+data['Dividend'], 2)
    # data=data[:-1]
    data.drop(columns=['Close', 'Money_invested_after_penalty', 'Avg_price'], inplace=True)
    return data

#only works if all rows has the same ticker
def get_ticker_currency(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> str:
    with open(path_to_json_file, "r") as f:
        j=json.load(f)
        currency=j[dataframe[isin_column_name].iloc[0]]['currency']
    return currency