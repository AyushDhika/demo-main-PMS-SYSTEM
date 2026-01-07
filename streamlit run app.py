import streamlit as st
import pandas as pd
import threading
import time
import sqlite3
import requests
from SmartApi import SmartConnect
import pyotp
from datetime import datetime

# ----------------- 1. CONFIG & DATABASE SETUP -----------------
st.set_page_config(page_title="Trade Nexus GOD MODE", layout="wide", page_icon="🦅")

# --- DATABASE HANDLER (Persist Data) ---
def init_db():
    conn = sqlite3.connect('tradenexus.db', check_same_thread=False)
    c = conn.cursor()
    # Table for Settings (Master Creds, Telegram)
    c.execute('''CREATE TABLE IF NOT EXISTS settings 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    # Table for Slaves
    c.execute('''CREATE TABLE IF NOT EXISTS slaves 
                 (name TEXT, api_key TEXT, client_id TEXT, password TEXT, totp TEXT, 
                  multiplier REAL, max_loss REAL, is_active INTEGER)''')
    conn.commit()
    return conn

conn = init_db()

# --- HELPER FUNCTIONS ---
def save_setting(key, value):
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

def get_setting(key):
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    return res[0] if res else ""

def add_slave_db(name, api, client, pwd, totp, mult, max_loss):
    c = conn.cursor()
    c.execute("INSERT INTO slaves VALUES (?,?,?,?,?,?,?,1)", 
              (name, api, client, pwd, totp, mult, max_loss))
    conn.commit()

def get_slaves_db():
    df = pd.read_sql("SELECT * FROM slaves", conn)
    return df

def delete_slave_db(client_id):
    c = conn.cursor()
    c.execute("DELETE FROM slaves WHERE client_id=?", (client_id,))
    conn.commit()

# --- STATE ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "master_api" not in st.session_state: st.session_state.master_api = None
if "copier_running" not in st.session_state: st.session_state.copier_running = False
if "logs" not in st.session_state: st.session_state.logs = []
if "processed_orders" not in st.session_state: st.session_state.processed_orders = set()
if "slave_instances" not in st.session_state: st.session_state.slave_instances = {} # Cache for API objects

# ----------------- 2. TELEGRAM & LOGGING -----------------
def send_telegram(message):
    bot_token = get_setting("tg_bot_token")
    chat_id = get_setting("tg_chat_id")
    if bot_token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": message})
        except:
            pass

def log_msg(msg, type="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = "white"
    if type == "trade": color = "#00ff88"
    if type == "error": color = "#ff0066"
    if type == "alert": color = "#f39c12"
    
    formatted_msg = f"[{ts}] {msg}"
    st.session_state.logs.insert(0, f"<span style='color:{color}'>{formatted_msg}</span>")
    
    # Send Telegram for Trades and Errors
    if type in ["trade", "error", "alert"]:
        send_telegram(f"{'✅' if type=='trade' else '❌' if type=='error' else '⚠️'} {msg}")

# ----------------- 3. LOGIN SYSTEM -----------------
def login_screen():
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("<h1 style='text-align:center; color:#00ff88;'>🦅 TRADE NEXUS PRO</h1>", unsafe_allow_html=True)
        with st.form("login"):
            user = st.text_input("Username")
            pwd = st.text_input("Password", type="password")
            if st.form_submit_button("LOGIN"):
                if user == "admin" and pwd == "admin":
                    st.session_state.logged_in = True
                    st.rerun()
                else:
                    st.error("Invalid Credentials")

if not st.session_state.logged_in:
    login_screen()
    st.stop()

# ----------------- 4. ANGEL ONE CONNECTOR -----------------
def connect_angel(api_key, client_id, password, totp_secret):
    try:
        obj = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        data = obj.generateSession(client_id, password, totp)
        if data['status']:
            return obj, "Success"
        return None, data['message']
    except Exception as e:
        return None, str(e)

# ----------------- 5. CORE ENGINE (Advanced) -----------------
def get_pnl(api_obj):
    try:
        positions = api_obj.position()
        if positions and 'data' in positions and positions['data']:
            total_pnl = 0.0
            for p in positions['data']:
                # Calculate PnL: (SellAvg - BuyAvg) * Qty + (LTP - BuyAvg) * OpenQty
                # Simplification: SmartAPI gives 'pnl' directly in some versions, but calculating is safer
                pnl = float(p.get('pnl', 0))
                total_pnl += pnl
            return total_pnl
        return 0.0
    except:
        return 0.0

def kill_switch_logic():
    log_msg("⚠️ KILL SWITCH ACTIVATED! SQUARING OFF ALL POSITIONS", "alert")
    slaves = get_slaves_db()
    for index, row in slaves.iterrows():
        cid = row['client_id']
        if cid in st.session_state.slave_instances:
            api = st.session_state.slave_instances[cid]
            try:
                positions = api.position()
                if positions and 'data' in positions:
                    for p in positions['data']:
                        qty = int(p['netqty'])
                        if qty != 0:
                            # Place Opposite Order
                            txn_type = "SELL" if qty > 0 else "BUY"
                            params = {
                                "variety": "NORMAL",
                                "tradingsymbol": p['tradingsymbol'],
                                "symboltoken": p['symboltoken'],
                                "transactiontype": txn_type,
                                "exchange": p['exchange'],
                                "ordertype": "MARKET",
                                "producttype": p['producttype'],
                                "duration": "DAY",
                                "quantity": abs(qty)
                            }
                            api.placeOrder(params)
                            log_msg(f"💀 Killed {p['tradingsymbol']} on {row['name']}", "trade")
            except Exception as e:
                log_msg(f"Failed to kill {row['name']}: {e}", "error")

def worker_slave_trade(slave_row, order_details):
    cid = slave_row['client_id']
    
    # 1. Connect if not in cache
    if cid not in st.session_state.slave_instances:
        api, msg = connect_angel(slave_row['api_key'], cid, slave_row['password'], slave_row['totp'])
        if api:
            st.session_state.slave_instances[cid] = api
        else:
            log_msg(f"Skipping {slave_row['name']} (Login Failed)", "error")
            return

    api = st.session_state.slave_instances[cid]
    
    # 2. Risk Check: Max Daily Loss
    current_pnl = get_pnl(api)
    max_loss_limit = -abs(float(slave_row['max_loss'])) # Ensure it's negative
    
    if current_pnl < max_loss_limit:
        log_msg(f"⛔ {slave_row['name']} Risk Stop! PnL {current_pnl} hit limit {max_loss_limit}", "alert")
        return

    # 3. Calculate Quantity
    master_qty = order_details['qty']
    slave_qty = int(master_qty * slave_row['multiplier'])
    
    # 4. Place Order
    try:
        params = {
            "variety": "NORMAL",
            "tradingsymbol": order_details['sym'],
            "symboltoken": order_details['token'],
            "transactiontype": order_details['txn'],
            "exchange": order_details['exch'],
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "quantity": slave_qty
        }
        api.placeOrder(params)
        log_msg(f"✅ {slave_row['name']} Executed {slave_qty} Qty", "trade")
    except Exception as e:
        log_msg(f"❌ {slave_row['name']} Order Failed: {e}", "error")

def engine_loop():
    while st.session_state.copier_running:
        try:
            # Re-login master if session expired (simplified)
            if not st.session_state.master_api:
                # Try reconnect logic here if needed
                pass

            orders = st.session_state.master_api.orderBook()
            if orders and 'data' in orders:
                for o in orders['data']:
                    if o['orderstatus'] == 'complete' and o['orderid'] not in st.session_state.processed_orders:
                        
                        st.session_state.processed_orders.add(o['orderid'])
                        
                        details = {
                            "sym": o['tradingsymbol'],
                            "txn": o['transactiontype'],
                            "qty": int(o['quantity']),
                            "token": o['symboltoken'],
                            "exch": o['exchange']
                        }
                        
                        log_msg(f"🦅 MASTER: {details['txn']} {details['qty']} {details['sym']}", "trade")
                        
                        # Fetch Slaves from DB
                        slaves = get_slaves_db()
                        
                        # Multithreaded Execution
                        threads = []
                        for idx, row in slaves.iterrows():
                            t = threading.Thread(target=worker_slave_trade, args=(row, details))
                            threads.append(t)
                            t.start()
                            
        except Exception as e:
            pass
        
        time.sleep(0.5)

def toggle_engine():
    if st.session_state.copier_running:
        st.session_state.copier_running = False
        log_msg("🛑 System Stopped", "alert")
    else:
        # Check Master Connection
        m_api = get_setting("m_api")
        m_client = get_setting("m_client")
        m_pass = get_setting("m_pass")
        m_totp = get_setting("m_totp")
        
        api, msg = connect_angel(m_api, m_client, m_pass, m_totp)
        if api:
            st.session_state.master_api = api
            
            # Sync existing orders
            try:
                ob = api.orderBook()
                if ob and 'data' in ob:
                    for o in ob['data']:
                        if o['orderstatus'] == 'complete':
                            st.session_state.processed_orders.add(o['orderid'])
            except: pass
            
            st.session_state.copier_running = True
            t = threading.Thread(target=engine_loop)
            t.start()
            log_msg("🚀 GOD MODE ACTIVATED", "trade")
        else:
            st.error(f"Master Connect Failed: {msg}")

# ----------------- 6. UI DASHBOARD -----------------
st.markdown("""
<style>
    .big-stat { font-size: 20px; font-weight: bold; color: white; }
    .stat-label { font-size: 12px; color: #aaa; }
    .stat-box { background: #1a1a1a; padding: 15px; border-radius: 10px; border: 1px solid #333; }
</style>
""", unsafe_allow_html=True)

# SIDEBAR: SETTINGS
with st.sidebar:
    st.header("⚙️ System Settings")
    with st.expander("🔑 Master Account", expanded=True):
        st.text_input("API Key", value=get_setting("m_api"), key="s_m_api")
        st.text_input("Client ID", value=get_setting("m_client"), key="s_m_client")
        st.text_input("Password", type="password", value=get_setting("m_pass"), key="s_m_pass")
        st.text_input("TOTP Secret", type="password", value=get_setting("m_totp"), key="s_m_totp")
        if st.button("Save Master"):
            save_setting("m_api", st.session_state.s_m_api)
            save_setting("m_client", st.session_state.s_m_client)
            save_setting("m_pass", st.session_state.s_m_pass)
            save_setting("m_totp", st.session_state.s_m_totp)
            st.success("Saved!")

    with st.expander("📱 Telegram Bot"):
        st.text_input("Bot Token", value=get_setting("tg_bot_token"), key="s_tg_tok")
        st.text_input("Chat ID", value=get_setting("tg_chat_id"), key="s_tg_chat")
        if st.button("Save Telegram"):
            save_setting("tg_bot_token", st.session_state.s_tg_tok)
            save_setting("tg_chat_id", st.session_state.s_tg_chat)
            send_telegram("🔔 Trade Nexus Connected!")
            st.success("Saved & Test Sent")

    st.markdown("---")
    st.header("➕ Add Slave")
    with st.form("add_slave"):
        n = st.text_input("Name")
        ak = st.text_input("API Key")
        ci = st.text_input("Client ID")
        pw = st.text_input("Password", type="password")
        to = st.text_input("TOTP", type="password")
        mu = st.number_input("Multiplier", 1.0, 10.0, 1.0)
        ml = st.number_input("Max Loss Limit (₹)", 500, 50000, 2000)
        
        if st.form_submit_button("Add Account"):
            add_slave_db(n, ak, ci, pw, to, mu, ml)
            st.success("Added!")
            st.rerun()

# MAIN AREA
c1, c2 = st.columns([2, 1])
with c1:
    st.title("🦅 GOD MODE DASHBOARD")
with c2:
    if st.button("☠️ KILL SWITCH (SQUARE OFF ALL)", type="primary"):
        kill_switch_logic()

# STATUS BAR
status = "ACTIVE" if st.session_state.copier_running else "OFFLINE"
color = "#00ff88" if st.session_state.copier_running else "#ff0066"
st.markdown(f"""
<div style='background:{color}; padding: 10px; border-radius: 5px; color: black; font-weight: bold; text-align: center;'>
    SYSTEM STATUS: {status}
</div>
<br>
""", unsafe_allow_html=True)

# LIVE P&L TABLE
st.subheader("📊 Live Positions & Risk")
if st.button("🔄 Refresh P&L"):
    slaves = get_slaves_db()
    pnl_data = []
    
    for idx, row in slaves.iterrows():
        # Connect if needed
        cid = row['client_id']
        if cid not in st.session_state.slave_instances:
            api, _ = connect_angel(row['api_key'], cid, row['password'], row['totp'])
            if api: st.session_state.slave_instances[cid] = api
        
        # Get PnL
        pnl = 0.0
        status = "Disconnected"
        if cid in st.session_state.slave_instances:
            pnl = get_pnl(st.session_state.slave_instances[cid])
            status = "Online"
            
        pnl_data.append({
            "Client": row['name'],
            "Status": status,
            "Multiplier": f"{row['multiplier']}x",
            "Max Loss": f"₹{row['max_loss']}",
            "Current P&L": f"₹{pnl:.2f}"
        })
    
    if pnl_data:
        st.dataframe(pd.DataFrame(pnl_data), use_container_width=True)

# CONTROLS AND LOGS
col_ctrl, col_logs = st.columns([1, 2])

with col_ctrl:
    st.subheader("🎮 Controls")
    if st.button("START / STOP ENGINE", use_container_width=True):
        toggle_engine()
        st.rerun()
    
    st.markdown("### 📋 Active Accounts")
    slaves = get_slaves_db()
    for idx, row in slaves.iterrows():
        with st.expander(f"{row['name']}"):
            if st.button("Delete", key=f"del_{row['client_id']}"):
                delete_slave_db(row['client_id'])
                st.rerun()

with col_logs:
    st.subheader("📜 Event Logs")
    log_box = st.container(height=400, border=True)
    for l in st.session_state.logs:
        log_box.markdown(l, unsafe_allow_html=True)
    if st.button("Refresh Logs"):
        st.rerun()
