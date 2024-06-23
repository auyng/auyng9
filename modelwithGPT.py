import time
import pyupbit
import datetime
import requests
import schedule
import os
import numpy as np

# API 키와 슬랙 토큰 등을 읽어옵니다.
with open("upbit.txt") as f:
    lines = f.readlines()
    access = lines[0].strip()
    secret = lines[1].strip()
    myToken = lines[2].strip()
    slackchannel = lines[3].strip()

# 구매 가격을 저장할 파일 경로
BUY_PRICE_FILE = "buy_price.txt"

def post_message(token, channel, text):
    """슬랙 메시지 전송"""
    response = requests.post("https://slack.com/api/chat.postMessage",
                             headers={"Authorization": "Bearer " + token},
                             data={"channel": channel, "text": text})
    if not response.ok:
        print(f"Failed to send message: {response.text}")

def get_target_price(ticker, k):
    """변동성 돌파 전략으로 매수 목표가 조회"""
    df = pyupbit.get_ohlcv(ticker, interval="minute60", count=2)
    target_price = df.iloc[0]['close'] + (df.iloc[0]['high'] - df.iloc[0]['low']) * k
    return target_price

def get_rsi(ticker, period=14):
    """RSI (Relative Strength Index) 조회"""
    df = pyupbit.get_ohlcv(ticker, interval="minute60", count=period+1)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_mfi(ticker, period=14):
    """MFI (Money Flow Index) 조회"""
    df = pyupbit.get_ohlcv(ticker, interval="minute60", count=period+1)
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    money_flow = typical_price * df['volume']
    positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0).rolling(window=period).sum()
    negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0).rolling(window=period).sum()
    mfi = 100 - (100 / (1 + positive_flow / negative_flow))
    return mfi.iloc[-1]

def get_balance(ticker):
    """잔고 조회"""
    balances = upbit.get_balances()
    for b in balances:
        if b['currency'] == ticker:
            if b['balance']:
                return float(b['balance'])
    return 0

def get_current_price(ticker):
    """현재가 조회"""
    return pyupbit.get_orderbook(ticker=ticker)["orderbook_units"][0]["ask_price"]

def get_buy_price(ticker):
    """구매 가격 조회"""
    file_path = f"{ticker.replace('-', '_')}_{BUY_PRICE_FILE}"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return float(f.read())
    return None

def set_buy_price(ticker, price):
    """구매 가격 저장"""
    file_path = f"{ticker.replace('-', '_')}_{BUY_PRICE_FILE}"
    with open(file_path, "w") as f:
        f.write(str(price))

# 로그인
upbit = pyupbit.Upbit(access, secret)
print("autotrade start")
# 시작 메시지 슬랙 전송
post_message(myToken, slackchannel, "autotrade start")

def trade(ticker, investment_per_coin):
    try:
        target_price = get_target_price(ticker, 0.5)
        current_price = get_current_price(ticker)
        rsi = get_rsi(ticker)
        mfi = get_mfi(ticker)
        buy_price = get_buy_price(ticker)  # 구매 가격 조회

        # 매수 조건
        buy_conditions = []

        if current_price > target_price:
            buy_conditions.append("Current price is above target price")

        if rsi < 30:
            buy_conditions.append("RSI is below 30 (oversold)")

        if mfi < 20:
            buy_conditions.append("MFI is below 20 (oversold)")

        if buy_conditions:
            krw = get_balance("KRW")
            if krw > 5000 and krw >= investment_per_coin:
                buy_result = upbit.buy_market_order(ticker, investment_per_coin * 0.9995)
                set_buy_price(ticker, current_price)  # 구매 가격 저장
                post_message(myToken, slackchannel, f"{ticker} buy: {buy_result}, Conditions: {', '.join(buy_conditions)}")

        # 매도 조건
        crypto = ticker.split('-')[1]
        crypto_balance = get_balance(crypto)
        if crypto_balance > 0.00008 and buy_price is not None:
            sell_conditions = []

            if current_price < buy_price * 0.95:
                sell_conditions.append("Price dropped below 95% of buy price")

            if current_price > buy_price * 1.05:
                sell_conditions.append("Price exceeded 105% of buy price")

            if rsi > 70:
                sell_conditions.append("RSI is above 70 (overbought)")

            if mfi > 80:
                sell_conditions.append("MFI is above 80 (overbought)")

            if sell_conditions:
                sell_result = upbit.sell_market_order(ticker, crypto_balance * 0.9995)
                post_message(myToken, slackchannel, f"{ticker} sell: {sell_result}, Conditions: {', '.join(sell_conditions)}")

    except Exception as e:
        print(e)
        post_message(myToken, slackchannel, str(e))

# 자동매매할 코인 리스트
tickers = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]  # 원하는 코인 티커를 추가
n = len(tickers)

def get_total_krw_balance():
    """총 원화 잔고 조회"""
    return get_balance("KRW")

# 각 코인에 대해 30분마다 trade 함수 실행
for ticker in tickers:
    schedule.every(30).minutes.do(lambda t=ticker: trade(t, get_total_krw_balance() / n))

# 자동매매 시작
while True:
    schedule.run_pending()
    time.sleep(1)
