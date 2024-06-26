import ccxt
import datetime
import pandas as pd
import math
import requests
import schedule
import os
import time
import hmac
import hashlib
import atexit

api_key = ""
secret = ""
myToken = ""
slackchannel = "#autotrade"

binance = ccxt.binance(config={
    'apiKey': api_key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

symbol = "BTC/USDT"
timedata = '2h'
futureleverage = 10

# 포지션 상태를 글로벌 변수로 설정
position = {
    "type": 'none',
    "amount": 0,
    "entry_price": 0,
    "initial_balance": 0
}

# 진입 금지 플래그
long_position_restriction = False
short_position_restriction = False

# 초기 자산 기록
initial_balance = 0

# 익절매 및 손절매 비율
take_profit_ratio = 0.05
stop_loss_ratio = 0.03

stop_loss_ratio_immediately = 0.005

def post_message(token, channel, text, attempts=3):
    """슬랙 메시지 전송"""
    for attempt in range(attempts):
        response = requests.post("https://slack.com/api/chat.postMessage",
                                 headers={"Authorization": "Bearer " + token},
                                 data={"channel": channel, "text": text})
        if response.ok:
            print(f"메시지 전송 성공:\n{text}")
            break
        else:
            print(f"메시지 전송 실패: {response.text}")
            time.sleep(1)

def generate_signature(query_string, secret):
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def set_isolated_margin_and_leverage(exchange, symbol, leverage):
    """Isolated 마진 모드와 레버리지 설정"""
    exchange.load_markets()
    market = exchange.market(symbol)
    
    base_url = 'https://fapi.binance.com'
    endpoint_margin = '/fapi/v1/marginType'
    timestamp = int(time.time() * 1000)
    params_margin = f'symbol={market["id"]}&marginType=ISOLATED&timestamp={timestamp}'
    signature_margin = generate_signature(params_margin, secret)
    url_margin = f"{base_url}{endpoint_margin}?{params_margin}&signature={signature_margin}"
    
    headers = {'X-MBX-APIKEY': api_key}
    
    response_margin = requests.post(url_margin, headers=headers)
    if response_margin.status_code == 200:
        post_message(myToken, slackchannel, "## Isolated 마진 모드 설정 성공 ##")
    else:
        post_message(myToken, slackchannel, f"!! Isolated 마진 모드 설정 오류 !!\n{response_margin.json()}")
    
    endpoint_leverage = '/fapi/v1/leverage'
    params_leverage = f'symbol={market["id"]}&leverage={leverage}&timestamp={timestamp}'
    signature_leverage = generate_signature(params_leverage, secret)
    url_leverage = f"{base_url}{endpoint_leverage}?{params_leverage}&signature={signature_leverage}"
    
    response_leverage = requests.post(url_leverage, headers=headers)
    if response_leverage.status_code == 200:
        post_message(myToken, slackchannel, f"## 레버리지 설정 성공 ##\n레버리지: {leverage}배\n코인: {symbol}\n마진 모드: ISOLATED")
    else:
        post_message(myToken, slackchannel, f"!! 레버리지 설정 실패 !!\n{response_leverage.json()}")

def get_balance():
    """잔고 조회"""
    balance = binance.fetch_balance()
    return balance['total']['USDT']

def get_current_price(symbol):
    """현재가 조회"""
    ticker = binance.fetch_ticker(symbol)
    return ticker['last']

def get_rsi(exchange, symbol, timeframe=timedata, period=14):
    """RSI (Relative Strength Index) 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=period+1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_macd(exchange, symbol, short_period=12, long_period=26, signal_period=9):
    """MACD (Moving Average Convergence Divergence) 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timedata, limit=long_period + signal_period)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    short_ema = df['close'].ewm(span=short_period, adjust=False).mean()
    long_ema = df['close'].ewm(span=long_period, adjust=False).mean()
    dif = short_ema - long_ema
    dea = dif.ewm(span=signal_period, adjust=False).mean()
    return dif.iloc[-1], dea.iloc[-1]

def cal_amount(usdt_balance, cur_price):
    """매매할 암호화폐 양 계산"""
    portion = 1
    usdt_trade = usdt_balance * portion * futureleverage
    amount = math.floor((usdt_trade * 1000000) / cur_price) / 1000000
    min_notional = 5
    if amount * cur_price < min_notional:
        amount = min_notional / cur_price
    return amount

# 포지션 진입 및 종료 함수
def enter_long_position(exchange, symbol, amount, cur_price):
    global position
    global long_position_restriction
    try:
        order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
        position['type'] = 'long'
        position['amount'] = amount
        position['entry_price'] = cur_price
        position['initial_balance'] = get_balance()
        entry_cost = amount * cur_price / futureleverage  # 진입 비용 계산
        post_message(myToken, slackchannel, f"## 롱 포지션 진입 ##\n코인: {symbol}\n진입 비용: {entry_cost:.2f} USDT\n진입 가격: {cur_price:.5f}")
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 롱 포지션 진입 실패 !!\n{e}")

def enter_short_position(exchange, symbol, amount, cur_price):
    global position
    global short_position_restriction
    try:
        order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
        position['type'] = 'short'
        position['amount'] = amount
        position['entry_price'] = cur_price
        position['initial_balance'] = get_balance()
        entry_cost = amount * cur_price / futureleverage  # 진입 비용 계산
        post_message(myToken, slackchannel, f"## 숏 포지션 진입 ##\n코인: {symbol}\n진입 비용: {entry_cost:.2f} USDT\n진입 가격: {cur_price:.5f}")
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 숏 포지션 진입 실패 !!\n{e}")

def exit_position(exchange, symbol, amount):
    global position
    global long_position_restriction
    global short_position_restriction
    try:
        final_price = get_current_price(symbol)
        if position['type'] == 'long':
            order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
            profit = (final_price - position['entry_price']) * amount
            profit_rate = (final_price / position['entry_price'] - 1) * 100
            post_message(myToken, slackchannel, f"## 롱 포지션 종료 ##\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
            long_position_restriction = True
            short_position_restriction = False
        elif position['type'] == 'short':
            order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
            profit = (position['entry_price'] - final_price) * amount
            profit_rate = (position['entry_price'] / final_price - 1) * 100
            post_message(myToken, slackchannel, f"## 숏 포지션 종료 ##\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
            long_position_restriction = False
            short_position_restriction = True
        position['type'] = 'none'
        position['amount'] = 0
        position['entry_price'] = 0
        position['initial_balance'] = 0
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 포지션 종료 실패 !!\n{e}")

# 통합 함수
def enter_position(exchange, symbol, cur_price, amount):
    global position
    global long_position_restriction
    global short_position_restriction
    dif, dea = get_macd(exchange, symbol)
    rsi = get_rsi(exchange, symbol)

    print(f"DEBUG: dif={dif}, dea={dea}, rsi={rsi}, cur_price={cur_price}")

    if position['type'] == 'none':
        if dif > dea and not long_position_restriction:
            enter_long_position(exchange, symbol, amount, cur_price)
        elif dif < dea and not short_position_restriction:
            enter_short_position(exchange, symbol, amount, cur_price)
    elif position['type'] == 'long':
        if dif > dea and rsi >= 75 and cur_price > position['entry_price']:
            post_message(myToken, slackchannel, f"롱 포지션 종료 조건 충족 (dif > dea, rsi >= 75, cur_price > entry_price)")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price >= position['entry_price'] * (1 + take_profit_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 롱 포지션 익절 ##")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price <= position['entry_price'] * (1 - stop_loss_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 롱 포지션 손절 ##")
            exit_position(exchange, symbol, position['amount'])
        # 반대 포지션 진입 조건 추가
        elif dif < dea and cur_price <= position['entry_price'] * (1 - stop_loss_ratio_immediately * futureleverage):
            post_message(myToken, slackchannel, "## 롱 포지션 종료 및 숏 포지션 진입 ##")
            exit_position(exchange, symbol, position['amount'])
            enter_short_position(exchange, symbol, amount, cur_price)
    elif position['type'] == 'short':
        if dif < dea and rsi <= 25 and cur_price < position['entry_price']:
            post_message(myToken, slackchannel, f"숏 포지션 종료 조건 충족 (dif < dea, rsi <= 25, cur_price < entry_price)")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price <= position['entry_price'] * (1 - take_profit_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 숏 포지션 익절 ##")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price >= position['entry_price'] * (1 + stop_loss_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 숏 포지션 손절 ##")
            exit_position(exchange, symbol, position['amount'])
        # 반대 포지션 진입 조건 추가
        elif dif > dea and cur_price >= position['entry_price'] * (1 + stop_loss_ratio_immediately * futureleverage):
            post_message(myToken, slackchannel, "## 숏 포지션 종료 및 롱 포지션 진입 ##")
            exit_position(exchange, symbol, position['amount'])
            enter_long_position(exchange, symbol, amount, cur_price)

# Trade
def trade(symbol):
    balance = get_balance()
    cur_price = get_current_price(symbol)
    amount = cal_amount(balance, cur_price)
    enter_position(binance, symbol, cur_price, amount)
    print(f"포지션 상태: {position}\n{datetime.datetime.now()} 현재 잔고: {balance:.2f}, 암호화폐: {symbol}\n현재가: {cur_price:.7f}")

    time.sleep(1)

# 자동매매 시작 알람에 추가 정보 포함
post_message(myToken, slackchannel, "@@ 선물거래 자동매매 시작 @@")

# 초기 자산 기록
initial_balance = get_balance()
set_isolated_margin_and_leverage(binance, symbol, futureleverage)

# 종료시 슬랙 알람
def notify_exit():
    final_balance = get_balance()
    profit = final_balance - initial_balance
    profit_rate = (final_balance / initial_balance - 1) * 100
    message = f"<선물거래 자동매매 종료>\n초기 자산: {initial_balance:.2f} USDT\n최종 자산: {final_balance:.2f} USDT\n총 수익: {profit:.2f} USDT\n수익률: {profit_rate:.2f} %"
    post_message(myToken, slackchannel, message)

# atexit 모듈을 사용하여 프로그램 종료 시 notify_exit 함수 호출
atexit.register(notify_exit)

# 각 코인에 대해 10초마다 trade 함수 실행
schedule.every(3).seconds.do(lambda: trade(symbol))

while True:
    schedule.run_pending()
    time.sleep(1)
