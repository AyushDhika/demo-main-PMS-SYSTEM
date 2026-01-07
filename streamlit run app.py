import streamlit as st
import threading
import time
from SmartApi import SmartConnect
import pyotp
from datetime import datetime

# ----------------- 1. APP CONFIGURATION -----------------
st.set_page_config(page_title="Trade Nexus Ultimate", layout="wide", page_icon="⚡")

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
        margin-bottom: 10px;
    }}
    .metric-value {{ font-size: 24px; font-weight: bold; color: white; }}
    .metric-label {{ font-size: 14px; color: #8899a6; }}
    .success-text {{ color: #00ff88; font-weight: bold; }}
    .error-text {{ color: #ff0066; font-weight: bold; }}
    .stButton>button {{
        font-weight: bold;
        border-radius: 5px;
    }}
    </style>
""", unsafe_allow_html=True)

# ----------------- 3. LOGIN SYSTEM -----------------
def login_screen():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(f"<h1 style='text-align:center; color:{COLOR_ACCENT};'>⚡ PORTFOLIO NEXUS ULTIMATE</h1>", unsafe_allow_html=True)
        
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
    st.stop()

# ----------------- 4. CONNECTION LOGIC -----------------
def connect_angel_master(api_key, client_id, password, totp_secret):
    try:
        obj = SmartConnect(api_key=api_key)
        try:
            totp = pyotp.TOTP(totp_secret).now()
        except:
            return None, None, "Invalid TOTP Secret (Use alphanumeric code)"
            
        data = obj.generateSession(client_id, password, totp)
        
        if data['status']:
            # Verify Profile & Funds
            try:
                rms = obj.rmsLimit()
                funds = float(rms['data']['net']) if rms and 'data' in rms else 0.0
                
                profile = obj.getProfile(data['data']['refreshToken'])
                name = profile['data']['name'] if profile and 'data' in profile else client_id
                
                info = {"name": name, "balance": funds, "client_id": client_id}
                return obj, info, "Success"
            except:
                # Fallback if profile fetch fails but login worked
                return obj, {"name": client_id, "balance": 0.0, "client_id": client_id}, "Success (Data Fetch Partial)"
        else:
            return None, None, data['message']
    except Exception as e:
        return None, None, str(e)

def connect_angel_slave(api_key, client_id, password, totp_secret):
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

# ----------------- 5. HIGH-SPEED ENGINE (MULTITHREADED) -----------------
def log_msg(msg, type="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = "white"
    if type == "trade": color = "#00ff88"
    if type == "error": color = "#ff0066"
    st.session_state.logs.insert(0, f"<span style='color:{color}'>[{ts}] {msg}</span>")

def place_slave_order_thread(slave, params):
    """Worker function for threads"""
    try:
        slave['api'].placeOrder(params)
        log_msg(f"✅ {slave['name']} Executed", "info")
    except Exception as e:
        log_msg(f"❌ {slave['name']} Failed: {e}", "error")

def copier_engine():
    """Main loop checking Master orders"""
    while st.session_state.copier_running:
        try:
            master = st.session_state.master_api
            if not master: break
            
            # Fetch Master Order Book
            try:
                orders = master.orderBook()
            except:
                orders = None # Handle API glitches

            if orders and 'data' in orders:
                for o in orders['data']:
                    # CHECK: Status is 'complete' AND we haven't processed this ID yet
                    if o['orderstatus'] == 'complete' and o['orderid'] not in st.session_state.processed_orders:
                        
                        # --- 1. CAPTURE DETAILS ---
                        oid = o['orderid']
                        sym = o['tradingsymbol']
                        txn = o['transactiontype']
                        qty = int(o['quantity'])
                        token = o['symboltoken']
                        exch = o['exchange']
                        
                        log_msg(f"🔔 MASTER SIGNAL: {txn} {qty} {sym}", "trade")
                        st.session_state.processed_orders.add(oid)
                        
                        # --- 2. MULTITHREADED EXECUTION (FAST) ---
                        threads = []
                        for slave in st.session_state.slaves:
                            slave_qty = int(qty * slave['multiplier'])
                            
                            params = {
                                "variety": "NORMAL",
                                "tradingsymbol": sym,
                                "symboltoken": token,
                                "transactiontype": txn,
                                "exchange": exch,
                                "ordertype": "MARKET", # Market order for speed
                                "producttype": "INTRADAY",
                                "duration": "DAY",
                                "quantity": slave_qty
                            }
                            
                            # Launch Thread
                            t = threading.Thread(target=place_slave_order_thread, args=(slave, params))
                            threads.append(t)
                            t.start()
                        
                        # Note: We do not join() threads here to allow the loop to continue instantly
                                
        except Exception as e:
            pass # Keep engine alive despite network blips
            
        # Fast Polling Speed (0.5s)
        time.sleep(0.5)

def toggle_engine():
    if st.session_state.copier_running:
        st.session_state.copier_running = False
        log_msg("🛑 Engine Stopped", "error")
    else:
        if not st.session_state.master_api:
            st.error("Connect Master Account First!")
        else:
            st.session_state.copier_running = True
            t = threading.Thread(target=copier_engine)
            t.start()
            log_msg("🚀 Engine Started - High Speed Mode", "trade")

# ----------------- 6. UI LAYOUT -----------------

# Header
c_head, c_out = st.columns([4, 1])
with c_head:
    st.markdown(f"<h2 style='color:{COLOR_ACCENT}'>🚀 PORTFOLIO NEXUS ULTIMATE</h2>", unsafe_allow_html=True)
with c_out:
    if st.button("LOGOUT"):
        st.session_state.logged_in = False
        st.rerun()

st.divider()

# --- SIDEBAR: CONNECTIONS ---
with st.sidebar:
    st.header("1. Master Setup")
    
    if st.session_state.master_api:
        st.success("Master Connected")  # Fixed potential string issue
        if st.button("Disconnect Master"):
            st.session_state.master_api = None
            st.session_state.master_info = {"name": "Not Connected", "balance": 0.0, "client_id": ""}
            st.session_state.copier_running = False
            st.rerun()
    else:
        with st.form("m_login"):
            mk = st.text_input("API Key")
            mi = st.text_input("Client ID")
            mp = st.text_input("Password", type="password")
            mt = st.text_input("TOTP Secret", type="password")
            if st.form_submit_button("CONNECT MASTER"):
                api, info, msg = connect_angel_master(mk, mi, mp, mt)
                if api:
                    st.session_state.master_api = api
                    st.session_state.master_info = info
                    # Sync Old Orders to avoid duplicates
                    try:
                        ob = api.orderBook()
                        if ob and 'data' in ob:
                            for o in ob['data']:
                                if o['orderstatus'] == 'complete':
                                    st.session_state.processed_orders.add(o['orderid'])
                    except: pass
                    st.success("Connected!")
                    st.rerun()
                else:
                    st.error(msg)

    st.markdown("---")
    st.header("2. Add Slave")
    with st.form("s_login"):
        sn = st.text_input("Name")
        sk = st.text_input("API Key")
        si = st.text_input("Client ID")
        sp = st.text_input("Password", type="password")
        stot = st.text_input("TOTP Secret", type="password")
        sm = st.number_input("Multiplier", value=1.0, step=0.5)
        
        if st.form_submit_button("ADD SLAVE"):
            api, msg = connect_angel_slave(sk, si, sp, stot)
            if api:
                st.session_state.slaves.append({
                    "name": sn, "client_id": si, "api": api, "multiplier": sm
                })
                st.success(f"Added {sn}")
            else:
                st.error(msg)

# --- DASHBOARD METRICS ---
info = st.session_state.master_info
is_conn = st.session_state.master_api is not None
run_state = "RUNNING" if st.session_state.copier_running else "STOPPED"
run_color = "#00ff88" if st.session_state.copier_running else "#ff0066"

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f"""<div class="metric-box"><div class="metric-label">Master Status</div>
    <div class="metric-value {'success-text' if is_conn else 'error-text'}">{'ONLINE' if is_conn else 'OFFLINE'}</div></div>""", unsafe_allow_html=True)
with m2:
    st.markdown(f"""<div class="metric-box"><div class="metric-label">Master Balance</div>
    <div class="metric-value">₹ {info['balance']:,.2f}</div></div>""", unsafe_allow_html=True)
with m3:
    st.markdown(f"""<div class="metric-box"><div class="metric-label">Active Slaves</div>
    <div class="metric-value">{len(st.session_state.slaves)}</div></div>""", unsafe_allow_html=True)
with m4:
    st.markdown(f"""<div class="metric-box"><div class="metric-label">Copier Engine</div>
    <div class="metric-value" style="color:{run_color}">{run_state}</div></div>""", unsafe_allow_html=True)

# --- CONTROLS & LOGS ---
c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("🎮 Control Center")
    if st.button("🚀 START / STOP ENGINE", type="primary", use_container_width=True):
        toggle_engine()
        st.rerun()

    st.markdown("### 👥 Slave Accounts")
    if st.session_state.slaves:
        for i, s in enumerate(st.session_state.slaves):
            with st.expander(f"{s['name']} ({s['multiplier']}x)"):
                st.write(f"ID: {s['client_id']}")
                if st.button("Remove", key=f"del_{i}"):
                    st.session_state.slaves.pop(i)
                    st.rerun()
    else:
        st.info("No slaves connected.")

with c2:
    st.subheader("📜 Live Execution Log")
    log_cont = st.container(height=400, border=True)
    for log in st.session_state.logs:
        log_cont.markdown(log, unsafe_allow_html=True)
    
    if st.button("Refresh Logs"):
        st.rerun()
