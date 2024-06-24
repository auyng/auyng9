import time
import pyupbit
import datetime
import requests
import schedule
import os
import numpy as np
import sqlite3
import atexit
import csv

# API 키와 슬랙 토큰 등을 읽어옵니다.
access = ""
secret = ""
myToken = ""
slackchannel = "#autotrade"
minutedata = "minute5"

# 자동매매할 코인 리스트
tickers = ["KRW-MTL", "KRW-HUNT"]  # 원하는 코인 티커를 추가
n = len(tickers)

# 거래 수수료 (0.05% = 0.0005)
FEE_RATE = 0.0005

# SQLite 데이터베이스 설정
conn = sqlite3.connect('C:/cryptoauto/upbit/trade_data.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS buy_price (ticker TEXT PRIMARY KEY, price REAL)''')
conn.commit()

# 거래 기록을 저장할 CSV 파일 경로
TRADE_HISTORY_FILE = "C:/cryptoauto/upbit/trade_history.csv"

# 거래 기록 CSV 파일이 존재하지 않으면 헤더를 추가
if not os.path.exists(TRADE_HISTORY_FILE):
    with open(TRADE_HISTORY_FILE, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "ticker", "type", "price", "volume", "conditions", "rsi", "mfi", "upper_band", "middle_band", "lower_band", "sentiment_index", "short_ma", "long_ma", "macd", "signal"])

def post_message(token, channel, text, attempts=3):
    """슬랙 메시지 전송"""
    for attempt in range(attempts):
        response = requests.post("https://slack.com/api/chat.postMessage",
                                 headers={"Authorization": "Bearer " + token},
                                 data={"channel": channel, "text": text})
        if response.ok:
            print(f"메시지 전송 성공: {text}")
            break
        else:
            print(f"메시지 전송 실패: {response.text}")
            time.sleep(1)

def get_target_price(ticker, k):
    """변동성 돌파 전략으로 매수 목표가 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=2)
    target_price = df.iloc[0]['close'] + (df.iloc[0]['high'] - df.iloc[0]['low']) * k
    return target_price

def get_rsi(ticker, period=14):
    """RSI (Relative Strength Index) 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=period+1)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_mfi(ticker, period=14):
    """MFI (Money Flow Index) 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=period+1)
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    money_flow = typical_price * df['volume']
    positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0).rolling(window=period).sum()
    negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0).rolling(window=period).sum()
    mfi = 100 - (100 / (1 + positive_flow / negative_flow))
    return mfi.iloc[-1]

def get_bollinger_bands(ticker, period=20):
    """볼린저밴드 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=period+1)
    tp = df['close']
    ma = tp.rolling(window=period).mean()
    std = tp.rolling(window=period).std()
    upper_band = ma + (std * 2)
    middle_band = ma  # 기준선
    lower_band = ma - (std * 2)
    return upper_band.iloc[-1], middle_band.iloc[-1], lower_band.iloc[-1]

def get_sentiment_index(ticker, period=14):
    """투자심리도 지표 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=period+1)
    close = df['close']
    sentiment = close.rolling(window=period).apply(lambda x: np.sum(x > x.mean()) / period * 100)
    return sentiment.iloc[-1]

def get_ma_cross(ticker, short_period=5, long_period=20):
    """이동 평균 교차 전략 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=long_period+1)
    short_ma = df['close'].rolling(window=short_period).mean()
    long_ma = df['close'].rolling(window=long_period).mean()
    return short_ma.iloc[-1], long_ma.iloc[-1]

def get_macd(ticker, short_period=12, long_period=26, signal_period=9):
    """MACD 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=minutedata, count=long_period + signal_period + 1)
    short_ema = df['close'].ewm(span=short_period, adjust=False).mean()
    long_ema = df['close'].ewm(span=long_period, adjust=False).mean()
    macd = short_ema - long_ema
    signal = macd.ewm(span=signal_period, adjust=False).mean()
    return macd.iloc[-1], signal.iloc[-1]

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
    c.execute("SELECT price FROM buy_price WHERE ticker = ?", (ticker,))
    result = c.fetchone()
    if result:
        return result[0]
    return None

def set_buy_price(ticker, price):
    """구매 가격 저장"""
    c.execute("INSERT OR REPLACE INTO buy_price (ticker, price) VALUES (?, ?)", (ticker, price))
    conn.commit()

def save_trade_history(ticker, trade_type, price, volume, conditions, rsi, mfi, upper_band, middle_band, lower_band, sentiment_index, short_ma, long_ma, macd, signal):
    """거래 기록을 CSV 파일에 저장"""
    with open(TRADE_HISTORY_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow([datetime.datetime.now(), ticker, trade_type, price, volume, conditions, rsi, mfi, upper_band, middle_band, lower_band, sentiment_index, short_ma, long_ma, macd, signal])

def calculate_minimum_profit_price(buy_price):
    """이익을 얻기 위한 최소 매도 가격 계산"""
    return buy_price * (1 + 2 * FEE_RATE)

def calculate_total_asset():
    """총 자산 조회"""
    total_krw = get_balance("KRW")
    for ticker in tickers:
        crypto = ticker.split('-')[1]
        balance = get_balance(crypto)
        if balance > 0:
            current_price = get_current_price(ticker)
            total_krw += balance * current_price
    return total_krw

# 로그인
upbit = pyupbit.Upbit(access, secret)
print("######업비트 자동매매 시작######")
# 시작 메시지 슬랙 전송
post_message(myToken, slackchannel, "######업비트 자동매매 시작######")

initial_balance = calculate_total_asset()  # 초기 자산 저장

last_action = {"type": None, "time": None} #플래그 사용

def trade(ticker, investment_per_coin):
    global last_action
    try:
        current_time = datetime.datetime.now()
        
        # 최근 액션이 매수 또는 매도 후 5분 이내인지 확인
        if last_action["time"] and (current_time - last_action["time"]).total_seconds() < 300:
            return
        
        target_price = get_target_price(ticker, 0.5)
        current_price = get_current_price(ticker)
        rsi = get_rsi(ticker)
        mfi = get_mfi(ticker)
        upper_band, middle_band, lower_band = get_bollinger_bands(ticker)
        sentiment_index = get_sentiment_index(ticker)
        short_ma, long_ma = get_ma_cross(ticker)
        macd, signal = get_macd(ticker)
        buy_price = get_buy_price(ticker)  # 구매 가격 조회

        # 매수 조건
        buy_conditions = []

        if current_price > target_price:
            buy_conditions.append("현재가 > 목표가")

        if rsi < 30:
            buy_conditions.append("RSI < 30 (과매도)")

        if mfi < 20:
            buy_conditions.append("MFI < 20 (과매도)")

        if sentiment_index < 20:
            buy_conditions.append("투자심리도 < 20 (매우 부정적)")

        if current_price < lower_band:
            buy_conditions.append("현재가 < 하단 볼린저밴드 기준선")

        if short_ma > long_ma:
            buy_conditions.append("단기 이동 평균 > 장기 이동 평균")

        if macd > signal:
            buy_conditions.append("MACD > 시그널선")

        if len(buy_conditions) >= 3:  # 매수 조건 중 최소 3개 이상 충족 시
            krw = get_balance("KRW")
            if krw > 5000 and krw >= investment_per_coin:
                buy_result = upbit.buy_market_order(ticker, investment_per_coin * (1-FEE_RATE))
                set_buy_price(ticker, current_price)  # 구매 가격 저장
                post_message(myToken, slackchannel, f"{ticker} 매수 완료: {buy_result}\n매수 금액: {investment_per_coin} KRW\n매수 가격: {current_price} KRW\n조건: {', '.join(buy_conditions)}")
                save_trade_history(ticker, "매수", current_price, investment_per_coin, ', '.join(buy_conditions), rsi, mfi, upper_band, middle_band, lower_band, sentiment_index, short_ma, long_ma, macd, signal)
                last_action = {"type": "buy", "time": current_time}

        # 매도 조건
        crypto = ticker.split('-')[1]
        crypto_balance = get_balance(crypto)
        minimum_profit_price = calculate_minimum_profit_price(buy_price) if buy_price else None
        if crypto_balance > 0.00008 and buy_price is not None:
            sell_conditions = []

            if current_price < buy_price * 0.95:
                sell_conditions.append("현재가 < 매수가의 95%")

            if current_price > minimum_profit_price:
                sell_conditions.append(f"현재가 > 매수가의 {round(1 + 2 * FEE_RATE, 4) * 100}%")

            if rsi > 60:
                sell_conditions.append("RSI > 60 (과매수)")

            if mfi > 70:
                sell_conditions.append("MFI > 70 (과매수)")

            if sentiment_index > 70:
                sell_conditions.append("투자심리도 > 70 (매우 긍정적)")

            if current_price > upper_band or current_price > middle_band:
                sell_conditions.append("현재가 > 상단 볼린저 밴드 또는 기준선")

            if short_ma < long_ma:
                sell_conditions.append("단기 이동 평균 < 장기 이동 평균")

            if macd < signal:
                sell_conditions.append("MACD < 시그널선")

            if len(sell_conditions) >= 3:  # 매도 조건 중 최소 3개 이상 충족 시
                sell_result = upbit.sell_market_order(ticker, crypto_balance * (1-FEE_RATE))
                sell_profit = (current_price - buy_price) * crypto_balance
                sell_profit_rate = (sell_profit / (buy_price * crypto_balance)) * 100
                post_message(myToken, slackchannel, f"{ticker} 매도 완료\n조건: {', '.join(sell_conditions)}\n매도 금액: {investment_per_coin} KRW\n매도 가격: {current_price} KRW\n수익: {sell_profit:.2f} KRW\n수익률: {sell_profit_rate:.2f}%")
                save_trade_history(ticker, "매도", current_price, crypto_balance, ', '.join(sell_conditions), rsi, mfi, upper_band, middle_band, lower_band, sentiment_index, short_ma, long_ma, macd, signal)
                last_action = {"type": "sell", "time": current_time}

    except Exception as e:
        print(e)
        post_message(myToken, slackchannel, str(e))


def get_total_krw_balance():
    """총 원화 잔고 조회"""
    return get_balance("KRW")

def calculate_total_profit(initial_krw, current_krw):
    """총 수익률 계산"""
    profit = current_krw - initial_krw
    profit_rate = (profit / initial_krw) * 100
    return profit, profit_rate

# 종료 시 슬랙 알림 보내기
def on_exit():
    try:
        final_asset = calculate_total_asset()
        total_profit, total_profit_rate = calculate_total_profit(initial_balance, final_asset)
        exit_message = f"!!업비트 자동매매 종료!!\n초기 자산: {initial_balance} KRW\n최종 자산: {final_asset} KRW\n총 수익: {total_profit} KRW\n총 수익률: {total_profit_rate:.2f}%"
        print(exit_message)  # 종료 메시지 출력
        post_message(myToken, slackchannel, exit_message)
    except Exception as e:
        print(f"종료 알림 중 오류 발생: {e}")
    finally:
        try:
            conn.close()  # SQLite 연결 해제
            print("SQLite 연결이 성공적으로 해제되었습니다.")
        except Exception as e:
            print(f"SQLite 연결 해제 중 오류 발생: {e}")

atexit.register(on_exit)

# 각 코인에 대해 5분마다 trade 함수 실행
for ticker in tickers:
    schedule.every(5).minutes.do(lambda t=ticker: trade(t, get_total_krw_balance() / n))

# 자동매매 시작
while True:
        schedule.run_pending()
        time.sleep(1)
