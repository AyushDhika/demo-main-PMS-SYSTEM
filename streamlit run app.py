import streamlit as st
import alpaca_trade_api as tradeapi
import pandas as pd
from datetime import datetime
import time

# --- CONFIGURATION & STYLING ---
st.set_page_config(page_title="ProTrade Copier", layout="wide")

st.markdown("""
<style>
    .metric-card {background-color: #f0f2f6; padding: 20px; border-radius: 10px; border-left: 5px solid #ff4b4b;}
    .stButton>button {width: 100%;}
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE MANAGEMENT ---
if 'accounts' not in st.session_state:
    st.session_state.accounts = [] # List to store API dicts
if 'logs' not in st.session_state:
    st.session_state.logs = []

# --- HELPER FUNCTIONS ---
def log_message(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.insert(0, f"[{timestamp}] {msg}")

def get_api_connection(api_key, api_secret, base_url="https://paper-api.alpaca.markets"):
    try:
        api = tradeapi.REST(api_key, api_secret, base_url, api_version='v2')
        account = api.get_account()
        return api, account
    except Exception as e:
        return None, None

def execute_copy_trade(symbol, side, qty, order_type, time_in_force):
    successful = 0
    failed = 0
    
    # Loop through all added accounts
    for acc in st.session_state.accounts:
        try:
            api = tradeapi.REST(acc['key'], acc['secret'], "https://paper-api.alpaca.markets", api_version='v2')
            
            # LOGIC: Here you could add multiplier logic (e.g., 2x size)
            # For now, we do exact quantity copy
            api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force=time_in_force
            )
            log_message(f"✅ Executed {side} {qty} {symbol} for {acc['name']}")
            successful += 1
        except Exception as e:
            log_message(f"❌ Failed for {acc['name']}: {str(e)}")
            failed += 1
            
    return successful, failed

# --- SIDEBAR: ACCOUNT MANAGEMENT ---
with st.sidebar:
    st.header("🔐 Account Manager")
    st.info("Currently supporting Alpaca Paper Trading")
    
    with st.expander("Add New Account"):
        acc_name = st.text_input("Account Name (e.g. Client A)")
        acc_key = st.text_input("API Key")
        acc_secret = st.text_input("Secret Key", type="password")
        
        if st.button("Connect Account"):
            if acc_name and acc_key and acc_secret:
                api, account_info = get_api_connection(acc_key, acc_secret)
                if account_info:
                    st.session_state.accounts.append({
                        "name": acc_name,
                        "key": acc_key,
                        "secret": acc_secret,
                        "id": account_info.id,
                        "cash": float(account_info.cash)
                    })
                    st.success(f"Connected: {acc_name}")
                else:
                    st.error("Invalid Credentials")
            else:
                st.warning("Fill all fields")

    st.divider()
    st.subheader(f"Active Accounts: {len(st.session_state.accounts)}")
    for acc in st.session_state.accounts:
        st.caption(f"👤 {acc['name']} | 💰 ${acc['cash']:,.2f}")

# --- MAIN DASHBOARD ---
st.title("🚀 Omni-Broker Trade Copier")

# Top Metrics
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Connected Equity", f"${sum([a['cash'] for a in st.session_state.accounts]):,.2f}")
with col2:
    st.metric("Active Slaves", len(st.session_state.accounts))
with col3:
    st.metric("System Status", "ONLINE", delta_color="normal")

st.divider()

# MASTER TERMINAL
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("📡 Master Trade Terminal")
    st.write("Orders placed here are broadcast to all connected accounts.")
    
    with st.form("master_order"):
        col_sym, col_qty = st.columns(2)
        with col_sym:
            symbol = st.text_input("Symbol (e.g. AAPL, TSLA)", value="AAPL").upper()
        with col_qty:
            qty = st.number_input("Quantity", min_value=1, value=1)
            
        col_side, col_type = st.columns(2)
        with col_side:
            side = st.selectbox("Side", ["buy", "sell"])
        with col_type:
            type_order = st.selectbox("Type", ["market", "limit"])
            
        submit = st.form_submit_button("🚀 EXECUTE MULTI-ACCOUNT ORDER")
        
        if submit:
            if len(st.session_state.accounts) == 0:
                st.error("No accounts connected! Add accounts in the sidebar.")
            else:
                log_message(f"📢 MASTER SIGNAL: {side.upper()} {qty} {symbol}")
                s, f = execute_copy_trade(symbol, side, qty, type_order, "gtc")
                if f == 0:
                    st.success(f"Trade copied successfully to {s} accounts.")
                else:
                    st.warning(f"Trade partially complete. Success: {s}, Failed: {f}")

with c2:
    st.subheader("📜 Live Event Log")
    log_container = st.container(height=400)
    for log in st.session_state.logs:
        log_container.text(log)

# --- PORTFOLIO OVERVIEW ---
st.subheader("📊 Aggregate Portfolio Positions")

if len(st.session_state.accounts) > 0:
    all_positions = []
    
    # Fetch positions for all accounts (This can be slow, normally done async)
    if st.button("Refresh Positions"):
        for acc in st.session_state.accounts:
            try:
                api = tradeapi.REST(acc['key'], acc['secret'], "https://paper-api.alpaca.markets", api_version='v2')
                positions = api.list_positions()
                for p in positions:
                    all_positions.append({
                        "Account": acc['name'],
                        "Symbol": p.symbol,
                        "Qty": p.qty,
                        "Entry Price": p.avg_entry_price,
                        "Current Price": p.current_price,
                        "P/L": f"${float(p.unrealized_pl):.2f}"
                    })
            except:
                pass
        
        if all_positions:
            df = pd.DataFrame(all_positions)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No open positions found.")
else:
    st.info("Connect accounts to view consolidated portfolio.")
