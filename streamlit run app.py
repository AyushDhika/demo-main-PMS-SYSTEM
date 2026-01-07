import streamlit as st
import pandas as pd
import threading
import time
from SmartApi import SmartConnect
import pyotp
from datetime import datetime

# ----------------- LOGIN PAGE (Same Logic as requested) -----------------
APP_USERNAME = "admin"
APP_PASSWORD = "admin"

def login():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.markdown(
        "<h2 style='text-align:center; color:#00ff88;'>🔒 Trade Nexus Copier Login</h2>",
        unsafe_allow_html=True
    )
    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login", use_container_width=True)
        if login_btn:
            if username == APP_USERNAME and password == APP_PASSWORD:
                st.session_state["authenticated"] = True
                st.success("Login successful! Loading dashboard...")
                st.rerun()
            else:
                st.error("Invalid username or password")
    return False

# ----------------- SESSION STATE SETUP -----------------
if "master_api" not in st.session_state:
    st.session_state.master_api = None
if "slaves" not in st.session_state:
    st.session_state.slaves = [] # List of {name, api, multiplier, client_id}
if "copier_running" not in st.session_state:
    st.session_state.copier_running = False
if "processed_orders" not in st.session_state:
    st.session_state.processed_orders = set()
if "logs" not in st.session_state:
    st.session_state.logs = []

# ----------------- PAGE CONFIG -----------------
st.set_page_config(page_title="🚀 Trade Nexus Copier", layout="wide", page_icon="⚡")

# Stop if not logged in
if not login():
    st.stop()

# ----------------- STYLE (Your preferred Dark/Neon Theme) -----------------
COLOR_ACCENT = "#00ff88"
COLOR_NEGATIVE = "#ff0066"
COLOR_BACKGROUND = "#0a192f"

st.markdown(f"""
    <style>
    .big-title {{
        text-align: center;
        color: {COLOR_ACCENT};
        font-size: 2.8em;
        text-shadow: 0 0 15px rgba(0, 255, 136, 0.3);
        margin-bottom: 0.2em;
    }}
    .subtitle {{
        text-align: center;
        color: #7f8ea3;
        margin-bottom: 2em;
    }}
    .metric-box {{
        padding: 1em;
        background: #16213e !important;
        border-radius: 12px !important;
        margin: 0.5em 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
        min-height: 110px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        border: 1px solid #30475e;
    }}
    .metric-label {{
        color: #8899a6;
        font-size: 0.9em;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    .metric-value {{
        color: white;
        font-size: 1.8em;
        font-weight: bold;
    }}
    .stButton>button {{
        background: linear-gradient(90deg,#00ff88,#0099ff);
        color: #0a192f;
        font-weight: bold;
        border-radius: 8px;
        border: none;
        transition: transform 0.1s;
    }}
    .stButton>button:hover {{
        transform: scale(1.02);
        color: white;
    }}
    </style>
""", unsafe_allow_html=True)

# ----------------- HELPER FUNCTIONS -----------------

def add_log(msg, type="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    icon = "ℹ️"
    if type == "success": icon = "✅"
    if type == "error": icon = "❌"
    if type == "trade": icon = "⚡"
    
    st.session_state.logs.insert(0, f"{timestamp} | {icon} {msg}")

def connect_angel(api_key, client_id, password, totp_secret):
    try:
        smartApi = SmartConnect(api_key=api_key)
        try:
            totp = pyotp.TOTP(totp_secret).now()
        except:
            return None, "Invalid TOTP Secret Code"
            
        data = smartApi.generateSession(client_id, password, totp)
        if data['status']:
            return smartApi, "Success"
        else:
            return None, data['message']
    except Exception as e:
        return None, str(e)

# ----------------- COPIER ENGINE (The Brain) -----------------
def copier_background_task():
    """
    Runs in a background thread. Monitors Master for completed orders.
    """
    while st.session_state.copier_running:
        try:
            if not st.session_state.master_api:
                break

            # Fetch Master Order Book
            orderbook = st.session_state.master_api.orderBook()
            
            if orderbook and 'data' in orderbook and orderbook['data']:
                for order in orderbook['data']:
                    # We look for COMPLETE orders that we haven't copied yet
                    if order['orderstatus'] == 'complete' and order['orderid'] not in st.session_state.processed_orders:
                        
                        # CAPTURE TRADE DETAILS
                        oid = order['orderid']
                        symbol = order['tradingsymbol']
                        trans_type = order['transactiontype'] # BUY/SELL
                        qty = int(order['quantity'])
                        token = order['symboltoken']
                        exchange = order['exchange']
                        
                        # Log detection
                        add_log(f"Master Executed: {trans_type} {qty} {symbol}", "trade")
                        
                        # Mark as processed immediately
                        st.session_state.processed_orders.add(oid)
                        
                        # EXECUTE ON SLAVES
                        for slave in st.session_state.slaves:
                            try:
                                slave_qty = int(qty * slave['multiplier'])
                                orderparams = {
                                    "variety": "NORMAL",
                                    "tradingsymbol": symbol,
                                    "symboltoken": token,
                                    "transactiontype": trans_type,
                                    "exchange": exchange,
                                    "ordertype": "MARKET",
                                    "producttype": "INTRADAY", # Defaulting to Intraday
                                    "duration": "DAY",
                                    "quantity": slave_qty
                                }
                                
                                # Send Order
                                res = slave['api'].placeOrder(orderparams)
                                add_log(f"-> {slave['name']} Copied ({slave_qty} Qty): ID {res}", "success")
                                
                            except Exception as e:
                                add_log(f"-> {slave['name']} Failed: {str(e)}", "error")
                                
        except Exception as e:
            # Silently catch polling errors to keep thread alive
            pass
        
        time.sleep(1.5) # Poll every 1.5 seconds

def toggle_copier():
    if not st.session_state.copier_running:
        if not st.session_state.master_api:
            st.error("Connect Master Account first!")
            return
        
        st.session_state.copier_running = True
        t = threading.Thread(target=copier_background_task)
        t.start()
        add_log("Copier Engine STARTED", "success")
    else:
        st.session_state.copier_running = False
        add_log("Copier Engine STOPPED", "error")

# ----------------- UI LAYOUT -----------------

st.markdown('<div class="big-title">⚡ TRADE NEXUS COPIER</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Multi-Account Angel One Automation System</div>', unsafe_allow_html=True)

# --- SIDEBAR (Account Connection) ---
with st.sidebar:
    st.header("🔑 Master Configuration")
    with st.expander("Connect Master Account", expanded=not bool(st.session_state.master_api)):
        m_apikey = st.text_input("Master API Key")
        m_client = st.text_input("Master Client ID")
        m_pass = st.text_input("Master Password", type="password")
        m_totp = st.text_input("Master TOTP Secret", type="password")
        
        if st.button("Link Master Account"):
            api, msg = connect_angel(m_apikey, m_client, m_pass, m_totp)
            if api:
                st.session_state.master_api = api
                # Sync existing orders so we don't copy old trades
                try:
                    ob = api.orderBook()
                    if ob and 'data' in ob and ob['data']:
                        for o in ob['data']:
                            if o['orderstatus'] == 'complete':
                                st.session_state.processed_orders.add(o['orderid'])
                except:
                    pass
                st.success("Master Linked Successfully!")
                st.rerun()
            else:
                st.error(f"Error: {msg}")

    st.markdown("---")
    st.header("👥 Slave Configuration")
    with st.form("add_slave"):
        s_name = st.text_input("Slave Name (e.g. Client A)")
        s_apikey = st.text_input("Slave API Key")
        s_client = st.text_input("Slave Client ID")
        s_pass = st.text_input("Password", type="password")
        s_totp = st.text_input("TOTP Secret", type="password")
        s_mult = st.number_input("Multiplier (e.g. 2.0 = Double Qty)", value=1.0, step=0.5)
        
        if st.form_submit_button("➕ Add Slave Account"):
            api, msg = connect_angel(s_apikey, s_client, s_pass, s_totp)
            if api:
                st.session_state.slaves.append({
                    "name": s_name,
                    "client_id": s_client,
                    "api": api,
                    "multiplier": s_mult
                })
                st.success(f"Added {s_name}")
                st.rerun()
            else:
                st.error(f"Slave Connection Failed: {msg}")

# --- MAIN DASHBOARD AREA ---

# 1. METRIC BOXES
m1, m2, m3, m4 = st.columns(4)

with m1:
    status_color = "#00ff88" if st.session_state.master_api else "#ff0066"
    status_text = "ONLINE" if st.session_state.master_api else "OFFLINE"
    st.markdown(f'''
        <div class="metric-box">
            <div class="metric-label">📡 MASTER STATUS</div>
            <div class="metric-value" style="color:{status_color}">{status_text}</div>
        </div>
    ''', unsafe_allow_html=True)

with m2:
    engine_color = "#00ff88" if st.session_state.copier_running else "#7f8ea3"
    engine_text = "RUNNING" if st.session_state.copier_running else "STOPPED"
    st.markdown(f'''
        <div class="metric-box">
            <div class="metric-label">⚙️ COPIER ENGINE</div>
            <div class="metric-value" style="color:{engine_color}">{engine_text}</div>
        </div>
    ''', unsafe_allow_html=True)

with m3:
    st.markdown(f'''
        <div class="metric-box">
            <div class="metric-label">👥 ACTIVE SLAVES</div>
            <div class="metric-value">{len(st.session_state.slaves)}</div>
        </div>
    ''', unsafe_allow_html=True)

with m4:
    trades_today = len([l for l in st.session_state.logs if "Master Executed" in l])
    st.markdown(f'''
        <div class="metric-box">
            <div class="metric-label">⚡ TRADES DETECTED</div>
            <div class="metric-value" style="color:#0099ff">{trades_today}</div>
        </div>
    ''', unsafe_allow_html=True)

st.markdown("---")

# 2. CONTROL CENTER
c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("🎮 Control Center")
    if st.button("🚀 START COPIER" if not st.session_state.copier_running else "🛑 STOP COPIER"):
        toggle_copier()
        st.rerun()

    st.markdown("### 📋 Connected Slaves")
    if st.session_state.slaves:
        for i, s in enumerate(st.session_state.slaves):
            with st.container():
                st.info(f"👤 **{s['name']}** ({s['client_id']}) | Multiplier: **{s['multiplier']}x**")
    else:
        st.warning("No slave accounts connected.")

with c2:
    st.subheader("📜 Live Event Log")
    
    # Create a container for logs
    log_container = st.container(height=400, border=True)
    
    if st.session_state.logs:
        for log in st.session_state.logs:
            log_container.text(log)
    else:
        log_container.caption("Waiting for events...")
        
    # Auto-refresh button for logs (since streamlit doesn't push updates automatically without interaction)
    if st.session_state.copier_running:
        if st.button("🔄 Refresh Logs"):
            st.rerun()
