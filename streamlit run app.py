import streamlit as st
import pandas as pd
import threading
import time
from SmartApi import SmartConnect
import pyotp
from datetime import datetime

# ----------------- 1. APP CONFIGURATION -----------------
st.set_page_config(page_title="Trade Nexus Pro", layout="wide", page_icon="⚡")

# --- CREDENTIALS ---
APP_USERNAME = "admin"
APP_PASSWORD = "admin"

# --- STATE INITIALIZATION ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "master_api" not in st.session_state:
    st.session_state.master_api = None
if "master_info" not in st.session_state:
    st.session_state.master_info = {"name": "Not Connected", "balance": 0.0, "client_id": ""}
if "slaves" not in st.session_state:
    st.session_state.slaves = []
if "copier_running" not in st.session_state:
    st.session_state.copier_running = False
if "processed_orders" not in st.session_state:
    st.session_state.processed_orders = set()
if "logs" not in st.session_state:
    st.session_state.logs = []

# ----------------- 2. CSS STYLING (Dark/Neon) -----------------
COLOR_ACCENT = "#00ff88"
COLOR_BG = "#0a192f"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {COLOR_BG}; }}
    .login-box {{
        padding: 30px;
        background-color: #16213e;
        border-radius: 15px;
        border: 1px solid #00ff88;
        box-shadow: 0 0 20px rgba(0, 255, 136, 0.2);
    }}
    .metric-box {{
        background: #16213e;
        padding: 15px;
        border-radius: 10px;
        border-left: 4px solid {COLOR_ACCENT};
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
    }}
    .metric-value {{ font-size: 24px; font-weight: bold; color: white; }}
    .metric-label {{ font-size: 14px; color: #8899a6; }}
    .success-text {{ color: #00ff88; font-weight: bold; }}
    .error-text {{ color: #ff0066; font-weight: bold; }}
    </style>
""", unsafe_allow_html=True)

# ----------------- 3. LOGIN SYSTEM (Fixed) -----------------
def login_screen():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.markdown(f"<h1 style='text-align:center; color:{COLOR_ACCENT};'>⚡ PORTFOLIO NEXUS LOGIN</h1>", unsafe_allow_html=True)
        
        with st.form("login_form"):
            user = st.text_input("Username")
            pwd = st.text_input("Password", type="password")
            submitted = st.form_submit_button("LOGIN", use_container_width=True)
            
            if submitted:
                if user == APP_USERNAME and pwd == APP_PASSWORD:
                    st.session_state.logged_in = True
                    st.rerun()
                else:
                    st.error("❌ Invalid Username or Password")

if not st.session_state.logged_in:
    login_screen()
    st.stop()  # Stop here if not logged in

# ----------------- 4. ANGEL ONE CONNECTION LOGIC -----------------
def connect_angel_master(api_key, client_id, password, totp_secret):
    try:
        obj = SmartConnect(api_key=api_key)
        
        # 1. Generate TOTP
        try:
            totp = pyotp.TOTP(totp_secret).now()
        except Exception:
            return None, None, "Invalid TOTP Secret (Ensure it's the alphanumeric code)"
            
        # 2. Generate Session
        data = obj.generateSession(client_id, password, totp)
        
        if data['status']:
            # 3. Fetch Profile & Funds to Verify
            try:
                # Fetch Funds
                rms = obj.rmsLimit()
                funds = "0.00"
                if rms and 'data' in rms:
                    # Logic to find net available funds
                    funds = rms['data'].get('net', 0)
                
                # Fetch Profile Name
                profile = obj.getProfile(data['data']['refreshToken'])
                name = profile['data']['name'] if profile and 'data' in profile else client_id
                
                info = {
                    "name": name,
                    "balance": float(funds),
                    "client_id": client_id
                }
                return obj, info, "Success"
            except Exception as e:
                return obj, {"name": client_id, "balance": 0.0}, "Connected, but failed to fetch funds."
        else:
            return None, None, data['message']
            
    except Exception as e:
        return None, None, str(e)

def connect_angel_slave(api_key, client_id, password, totp_secret):
    # Simpler connection for slaves (we don't strictly need balance to trade, but good to check login)
    try:
        obj = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        data = obj.generateSession(client_id, password, totp)
        if data['status']:
            return obj, "Success"
        else:
            return None, data['message']
    except Exception as e:
        return None, str(e)

# ----------------- 5. BACKGROUND COPIER LOGIC -----------------
def log_msg(msg, type="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = "white"
    if type == "trade": color = "#00ff88" # Green
    if type == "error": color = "#ff0066" # Red
    st.session_state.logs.insert(0, f"<span style='color:{color}'>[{ts}] {msg}</span>")

def copier_engine():
    while st.session_state.copier_running:
        try:
            master = st.session_state.master_api
            if not master: break
            
            # Fetch Order Book
            orders = master.orderBook()
            
            if orders and 'data' in orders:
                for o in orders['data']:
                    # CHECK: Status is Complete AND we haven't processed it yet
                    if o['orderstatus'] == 'complete' and o['orderid'] not in st.session_state.processed_orders:
                        
                        # New Trade Detected
                        oid = o['orderid']
                        sym = o['tradingsymbol']
                        txn = o['transactiontype']
                        qty = int(o['quantity'])
                        token = o['symboltoken']
                        exch = o['exchange']
                        
                        log_msg(f"🔔 MASTER: {txn} {qty} {sym}", "trade")
                        st.session_state.processed_orders.add(oid)
                        
                        # Broadcast to Slaves
                        for slave in st.session_state.slaves:
                            try:
                                slave_qty = int(qty * slave['multiplier'])
                                params = {
                                    "variety": "NORMAL",
                                    "tradingsymbol": sym,
                                    "symboltoken": token,
                                    "transactiontype": txn,
                                    "exchange": exch,
                                    "ordertype": "MARKET",
                                    "producttype": "INTRADAY",
                                    "duration": "DAY",
                                    "quantity": slave_qty
                                }
                                slave['api'].placeOrder(params)
                                log_msg(f"✅ {slave['name']}: Sent {slave_qty} Qty", "info")
                            except Exception as e:
                                log_msg(f"❌ {slave['name']} Failed: {e}", "error")
                                
        except Exception as e:
            # Keep running even if internet flickers
            pass
        time.sleep(1.5)

def toggle_engine():
    if st.session_state.copier_running:
        st.session_state.copier_running = False
        log_msg("🛑 Copier Stopped", "error")
    else:
        if not st.session_state.master_api:
            st.error("Connect Master Account First!")
        else:
            st.session_state.copier_running = True
            t = threading.Thread(target=copier_engine)
            t.start()
            log_msg("🚀 Copier Started - Monitoring Master", "trade")

# ----------------- 6. DASHBOARD UI -----------------

# Header
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.markdown(f"<h1 style='color:{COLOR_ACCENT}'>🚀 PORTFOLIO NEXUS PRO</h1>", unsafe_allow_html=True)
with col_h2:
    if st.button("LOGOUT"):
        st.session_state.logged_in = False
        st.rerun()

st.divider()

# --- CONNECTION PANEL (SIDEBAR) ---
with st.sidebar:
    st.header("1. Master Account")
    
    # Logic: If already connected, hide the form inputs
    if st.session_state.master_api:
        st.success("Master Connected ✅")
        if st.button("Disconnect Master"):
            st.session_state.master_api = None
            st.session_state.master_info = {"name": "Not Connected", "balance": 0.0, "client_id": ""}
            st.rerun()
    else:
        with st.form("master_login"):
            m_key = st.text_input("API Key")
            m_id = st.text_input("Client ID")
            m_pass = st.text_input("Password", type="password")
            m_totp = st.text_input("TOTP Secret", type="password")
            
            if st.form_submit_button("CONNECT MASTER"):
                api, info, msg = connect_angel_master(m_key, m_id, m_pass, m_totp)
                if api:
                    st.session_state.master_api = api
                    st.session_state.master_info = info
                    # Sync existing orders
                    try:
                        obs = api.orderBook()
                        if obs and 'data' in obs:
                            for o in obs['data']:
                                if o['orderstatus'] == 'complete':
                                    st.session_state.processed_orders.add(o['orderid'])
                    except: pass
                    st.success("Connected!")
                    st.rerun()
                else:
                    st.error(msg)

    st.markdown("---")
    st.header("2. Slave Accounts")
    
    with st.form("slave_login"):
        s_name = st.text_input("Slave Name")
        s_key = st.text_input("API Key")
        s_id = st.text_input("Client ID")
        s_pass = st.text_input("Password", type="password")
        s_totp = st.text_input("TOTP Secret", type="password")
        s_mult = st.number_input("Multiplier", value=1.0)
        
        if st.form_submit_button("ADD SLAVE"):
            api, msg = connect_angel_slave(s_key, s_id, s_pass, s_totp)
            if api:
                st.session_state.slaves.append({
                    "name": s_name, 
                    "client_id": s_id, 
                    "api": api, 
                    "multiplier": s_mult
                })
                st.success(f"Added {s_name}")
            else:
                st.error(msg)

# --- MAIN METRICS ---
m1, m2, m3, m4 = st.columns(4)

info = st.session_state.master_info
is_conn = st.session_state.master_api is not None

with m1:
    st.markdown(f"""
    <div class="metric-box">
        <div class="metric-label">Master Status</div>
        <div class="metric-value {'success-text' if is_conn else 'error-text'}">
            {'CONNECTED' if is_conn else 'DISCONNECTED'}
        </div>
        <div style="font-size:12px; color:#ccc;">{info['client_id']}</div>
    </div>
    """, unsafe_allow_html=True)

with m2:
    st.markdown(f"""
    <div class="metric-box">
        <div class="metric-label">Master Account Name</div>
        <div class="metric-value">{info['name']}</div>
    </div>
    """, unsafe_allow_html=True)

with m3:
    st.markdown(f"""
    <div class="metric-box">
        <div class="metric-label">Available Funds</div>
        <div class="metric-value">₹ {info['balance']:,.2f}</div>
    </div>
    """, unsafe_allow_html=True)

with m4:
    run_state = "RUNNING" if st.session_state.copier_running else "STOPPED"
    run_color = "#00ff88" if st.session_state.copier_running else "#ff0066"
    st.markdown(f"""
    <div class="metric-box">
        <div class="metric-label">Copier Engine</div>
        <div class="metric-value" style="color:{run_color}">{run_state}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- CONTROLS AND LOGS ---
c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("🎮 Actions")
    
    if st.button("🚀 START / STOP COPIER", type="primary", use_container_width=True):
        toggle_engine()
        st.rerun()
        
    st.markdown("### Connected Slaves")
    if len(st.session_state.slaves) > 0:
        for i, s in enumerate(st.session_state.slaves):
            st.info(f"👤 {s['name']} ({s['multiplier']}x)")
            if st.button(f"Remove", key=f"rm_{i}"):
                st.session_state.slaves.pop(i)
                st.rerun()
    else:
        st.caption("No slaves added yet.")

with c2:
    st.subheader("📜 Live Activity Log")
    log_box = st.container(height=400, border=True)
    for l in st.session_state.logs:
        log_box.markdown(l, unsafe_allow_html=True)
        
    if st.button("Refresh Logs"):
        st.rerun()
