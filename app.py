import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os
import requests
import streamlit.components.v1 as components
import google.generativeai as genai
from streamlit_autorefresh import st_autorefresh
from fpdf import FPDF
import unicodedata
import base64
import numpy as np
import re
from google.cloud import firestore
import google.oauth2.service_account

# =========================================================================
# 🔥 CONEXÃO COM O FIREBASE (PARA OS LOGS LEVES)
# =========================================================================
@st.cache_resource
def conectar_firebase():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        credentials = google.oauth2.service_account.Credentials.from_service_account_info(creds_dict)
        db = firestore.Client(credentials=credentials, project=credentials.project_id)
        return db
    except Exception as e:
        return None

db = conectar_firebase()

# =========================================================================
# 🛡️ AIRBAG ANTI-QUOTA DO GOOGLE (O SALVA-FÉRIAS) - VERSÃO MAX
# =========================================================================
original_update_cell = gspread.worksheet.Worksheet.update_cell
original_append_row = gspread.worksheet.Worksheet.append_row

def blindagem_update_cell(self, row, col, val):
    for tentativa in range(12):
        try:
            return original_update_cell(self, row, col, val)
        except Exception as e:
            if "429" in str(e) or "Quota" in str(e) or "quota" in str(e).lower():
                time.sleep(8)
            else:
                raise e
    return original_update_cell(self, row, col, val)

def blindagem_append_row(self, values, **kwargs):
    for tentativa in range(12):
        try:
            return original_append_row(self, values, **kwargs)
        except Exception as e:
            if "429" in str(e) or "Quota" in str(e) or "quota" in str(e).lower():
                time.sleep(8)
            else:
                raise e
    return original_append_row(self, values, **kwargs)

gspread.worksheet.Worksheet.update_cell = blindagem_update_cell
gspread.worksheet.Worksheet.append_row = blindagem_append_row
# =========================================================================

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Esteira Qualitor", page_icon="🎫", layout="wide")

# --- 👑 ADMINISTRAÇÃO E SQUADS ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor", "Lopes", "eduardosouza", "biancamoura", "andreacastro"] 

SQUAD_AZIX = ["charleneoliveira", "brunasouza2", "viniciosmarques2"] 
SQUAD_MKTP = ["vitoriabraga", "fabiolapereira"] 
SQUAD_ATIVAS = ["Ruan Athaide", "Camila Garcia", "Marlise Borges", "Daiane Habowski", "Yasmine Goulart", "Raissa Silva", "Roger Santos", "Bianca Brasil", "Andressa Marchaki", "Viviane Santos", "Joice Machado", "Endrio Silva", "Alex Alves", "Franscielle Leal", "Sophie Barbosa"]

# --- META DIÁRIA E CELEBRAÇÃO ---
META_DIARIA = 50

def verificar_meta_baloes(usuario_atual, df_ranking, meta):
    feitos_hoje = 0
    if not df_ranking.empty:
        minha_linha = df_ranking[df_ranking['Nome'] == usuario_atual]
        if not minha_linha.empty:
            feitos_hoje = int(minha_linha.iloc[0]['Qtd'])
    
    feitos_agora = feitos_hoje + 1
    if feitos_agora in [10, 25, meta, 75, 100]:
        st.session_state['soltar_baloes'] = True

# --- 🧠 CONFIGURAÇÃO DA IA (ORÁCULO E RESUMIDOR) ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    modelo_escolhido = None
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            if 'flash' in m.name: 
                modelo_escolhido = m.name
                break
    if not modelo_escolhido:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelo_escolhido = m.name
                break
    if modelo_escolhido and modelo_escolhido.startswith("models/"):
        modelo_escolhido = modelo_escolhido.replace("models/", "")
        
    modelo_oraculo = genai.GenerativeModel(modelo_escolhido)
    ia_ativa = True
except Exception as e:
    modelo_oraculo = None
    ia_ativa = False
    erro_ia = str(e)

# --- 🚨 ALERTA MICROSOFT TEAMS ---
def alertar_teams(mensagem):
    webhook_url = "https://frigelar.webhook.office.com/webhookb2/ec98f756-9855-46a4-a0c4-084062e87994@d8d0f357-174f-48d7-b3b2-a5a630b0cd99/IncomingWebhook/4282a43bc29f475d9d1ca3629f01fcd6/5cd3c896-0830-48e2-9541-84f9563e933b/V2CUlXhnGitZt59misdhM4o9QEsdDxbpLgnT7PUUALYJc1"
    if webhook_url != "https://teams.microsoft.com/l/chat/48:notes/conversations?context=%7B%22contextType%22%3A%22chat%22%7D":
        try: requests.post(webhook_url, json={"text": mensagem})
        except: pass

# --- CONEXÃO BLINDADA (ANTI-TRAVAMENTO) ---
@st.cache_resource
def conectar_e_abrir_abas():
    try:
        if os.path.exists("credentials.json"):
            client = gspread.service_account(filename="credentials.json")
        else:
            try:
                creds_dict = st.secrets["gcp_service_account"]
                client = gspread.service_account_from_dict(creds_dict)
            except:
                return "🚨 ERRO: Credenciais não encontradas.", None, None, None, None, None, None
        
        erro_real = ""
        for tentativa in range(10):
            try:
                sh = client.open("Chamados_Qualitor") 
                abas = sh.worksheets()
                if len(abas) >= 2:
                    aba_chamados = abas[0] if len(abas) > 0 else None
                    aba_users = abas[1] if len(abas) > 1 else None
                    aba_logs = abas[2] if len(abas) > 2 else None
                    aba_transp = abas[3] if len(abas) > 3 else None
                    aba_azix = abas[4] if len(abas) > 4 else None 
                    aba_ativas = abas[5] if len(abas) > 5 else None 
                    
                    return sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix, aba_ativas
                else: erro_real = "A planilha tem menos de 2 abas visíveis."
            except Exception as e:
                erro_real = str(e)
                time.sleep(2 + tentativa)
                
        return f"Falha após 10 tentativas. Erro: {erro_real}", None, None, None, None, None, None
    except Exception as e:
        return f"Erro Crítico: {str(e)}", None, None, None, None, None, None

# --- FUNÇÕES DE TEMPO E SLA ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso) 

def hora_texto(): return hora_brasil().strftime("%d/%m/%Y %H:%M:%S")
def data_hoje(): return hora_brasil().strftime("%d/%m/%Y")

def calcular_duracao_str(inicio_str, fim_str):
    try:
        fmt = "%d/%m/%Y %H:%M:%S"
        inicio = datetime.strptime(str(inicio_str), fmt)
        fim = datetime.strptime(str(fim_str), fmt)
        minutos = int((fim - inicio).total_seconds() / 60)
        if minutos < 0: return "0m"
        horas, resto = divmod(minutos, 60)
        if horas > 0: return f"{horas}h {resto}m"
        return f"{resto}m"
    except: return "-"

def calcular_sla_bizdays(data_entrada_str):
    if pd.isna(data_entrada_str) or str(data_entrada_str).strip() == "" or str(data_entrada_str).lower() == "nan":
        return "⏳ A calcular..."
    try:
        data_str = str(data_entrada_str).split(" ")[0]
        data_entrada = datetime.strptime(data_str, "%d/%m/%Y").date()
        hoje = hora_brasil().date()
        
        dias_uteis = len(pd.bdate_range(start=data_entrada, end=hoje)) - 1
        if dias_uteis < 0: dias_uteis = 0
        
        if dias_uteis <= 2: return f"✅ No Prazo ({dias_uteis}/3 dias)"
        elif dias_uteis == 3: return f"🔥 Vence Hoje (3/3 dias)"
        else: return f"🚨 Atrasado ({dias_uteis} dias úteis)"
    except: return "⏳ A calcular..."

# --- INICIAR CONEXÃO ---
sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix, aba_ativas = conectar_e_abrir_abas()
if isinstance(sh, str): st.error(f"Erro de conexão: {sh}"); st.stop()
if aba_chamados is None: st.error("Erro ao carregar abas principais."); st.stop()

# =========================================================================
# 🔥 REGISTO DE LOGS NO FIREBASE (ZERO PESO NA MEMÓRIA)
# =========================================================================
def registrar_log(usuario, acao):
    try:
        if db is not None:
            db.collection('logs_qualitor').add({
                "Usuario": str(usuario),
                "Acao": str(acao),
                "DataHora": hora_texto()
            })
    except: pass

@st.cache_data(ttl=60, max_entries=2) 
def carregar_logs_dia():
    if db is None: 
        return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])
    try:
        docs = db.collection('logs_qualitor').stream()
        lista_logs = [doc.to_dict() for doc in docs]
        
        if not lista_logs: 
            return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])
            
        df = pd.DataFrame(lista_logs)
        for col in ["Usuario", "Acao", "DataHora"]:
            if col not in df.columns: df[col] = ""
                
        df = df[["Usuario", "Acao", "DataHora"]]
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        return df
    except:
        return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])

# =========================================================================
# 🔄 MOTORES DE DADOS (FILAS NA PLANILHA)
# =========================================================================
@st.cache_data(ttl=120, max_entries=2)
def carregar_dados_chamados():
    try:
        dados = aba_chamados.get('A1:AZ2000') 
        if not dados or len(dados) < 2: return pd.DataFrame()
        df = pd.DataFrame(dados)
        df.columns = df.iloc[0].astype(str)
        df = df[1:].reset_index(drop=True)
        df = df.loc[:, df.columns != ''] 
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        if 'Dados' in df.columns: df['Dados'] = df['Dados'].astype(str).str.replace(r'\.0$', '', regex=True)
        if 'Status' in df.columns: df['Status'] = df['Status'].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=120, max_entries=2)
def carregar_dados_azix():
    if aba_azix is None: return pd.DataFrame()
    try:
        dados = aba_azix.get('A1:AZ2000') 
        if not dados or len(dados) < 2: return pd.DataFrame()
        df = pd.DataFrame(dados)
        df.columns = df.iloc[0].astype(str)
        df = df[1:].reset_index(drop=True)
        df = df.loc[:, df.columns != ''] 
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        if 'Nº Pedido venda' in df.columns: df['Nº Pedido venda'] = df['Nº Pedido venda'].astype(str).str.replace(r'\.0$', '', regex=True)
        if 'Status' in df.columns: df['Status'] = df['Status'].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=120, max_entries=2)
def carregar_dados_ativas():
    if aba_ativas is None: return pd.DataFrame()
    try:
        dados = aba_ativas.get('A1:AZ2000') 
        if not dados or len(dados) < 2: return pd.DataFrame()
        df = pd.DataFrame(dados)
        df.columns = df.iloc[0].astype(str)
        df = df[1:].reset_index(drop=True)
        df = df.loc[:, df.columns != ''] 
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        if 'Pedido' in df.columns: df['Pedido'] = df['Pedido'].astype(str).str.replace(r'\.0$', '', regex=True)
        if 'Status' in df.columns: df['Status'] = df['Status'].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=120, max_entries=2)
def carregar_status_equipe():
    try:
        dados = aba_users.get('A1:Z500')
        if not dados or len(dados) < 2: return pd.DataFrame()
        df = pd.DataFrame(dados)
        df.columns = df.iloc[0].astype(str)
        df = df[1:].reset_index(drop=True)
        return df.loc[:, df.columns != ''] 
    except: return pd.DataFrame()

@st.cache_data(ttl=3600, max_entries=2)
def carregar_agenda_transp():
    if aba_transp is None: return pd.DataFrame()
    try: return pd.DataFrame(aba_transp.get_all_records())
    except: return pd.DataFrame()

def ler_mural():
    try:
        with open("mural.txt", "r", encoding="utf-8") as f: return f.read().strip()
    except: return ""

def salvar_mural(texto):
    with open("mural.txt", "w", encoding="utf-8") as f: f.write(texto)

def is_marketplace(texto):
    palavras_chave = ['MAGAZINE', 'MERCADO', 'B2W', 'AMAZON', 'SHOPEE', 'CARREFOUR']
    return any(k in str(texto).upper() for k in palavras_chave)

# --- RANKING GLOBAL ---
df_logs_global = carregar_logs_dia()
ranking_global = pd.DataFrame()
if not df_logs_global.empty:
    hoje = data_hoje()
    logs_hoje = df_logs_global[df_logs_global['DataHora'].astype(str).str.contains(hoje)]
    feitos_logs = logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Finalizou|Encerrada|Concluiu Azix|Concluiu Ativa|Reivindicação Encerrada", case=False)]
    if not feitos_logs.empty:
        ranking_global = feitos_logs['Usuario'].value_counts().reset_index()
        ranking_global.columns = ['Nome', 'Qtd']

if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons(); st.session_state['soltar_baloes'] = False

if 'tema_escolhido' not in st.session_state: st.session_state['tema_escolhido'] = "Padrão"

# ===================================================
# 🎫 TELA DE LOGIN CORPORATIVA BLINDADA
# ===================================================
def render_corporate_login():
    st.markdown("""
    <style>
        .stApp { background: linear-gradient(135deg, #0f1c3a 0%, #173775 100%) !important; }
        [data-testid="stSidebar"], [data-testid="stHeader"] { display: none !important; }
        .main .block-container { max-width: 1200px !important; padding-top: 8vh !important; }
        [data-testid="stForm"] { background-color: #ffffff !important; padding: 50px 40px !important; border-radius: 15px !important; box-shadow: 0 15px 35px rgba(0,0,0,0.5) !important; border: none !important; width: 100% !important; max-width: 480px !important; margin: 0 auto !important; }
        [data-testid="stForm"] div[data-baseweb="input"] { background-color: #ffffff !important; border: 2px solid #e2e8f0 !important; border-radius: 8px !important; transition: all 0.3s ease !important; height: 55px !important; }
        [data-testid="stForm"] input { color: #0f172a !important; -webkit-text-fill-color: #0f172a !important; font-size: 1.1em !important; height: 55px !important; line-height: 55px !important; padding-top: 0px !important; padding-bottom: 0px !important; background-color: transparent !important; }
        [data-testid="stForm"] input::placeholder { color: #a0aec0 !important; -webkit-text-fill-color: #a0aec0 !important; opacity: 1 !important; line-height: 55px !important; }
        [data-testid="stForm"] input[type="text"] { background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 448 512'%3E%3Cpath fill='%23a0aec0' d='M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3C0 498.7 13.3 512 29.7 512l388.6 0c16.4 0 29.7-13.3 29.7-29.7C448 383.8 368.2 304 269.7 304l-91.4 0z'/%3E%3C/svg%3E") !important; background-repeat: no-repeat !important; background-position: 15px 50% !important; background-size: 18px !important; padding-left: 50px !important; }
        [data-testid="stForm"] input[type="password"] { background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 448 512'%3E%3Cpath fill='%23a0aec0' d='M144 144v48H304V144c0-44.2-35.8-80-80-80s-80 35.8-80 80zM80 192V144C80 64.5 144.5 0 224 0s144 64.5 144 144v48h16c35.3 0 64 28.7 64 64V448c0 35.3-28.7 64-64 64H64c-35.3 0-64-28.7-64-64V256c0-35.3 28.7-64 64-64H80z'/%3E%3C/svg%3E") !important; background-repeat: no-repeat !important; background-position: 15px 50% !important; background-size: 16px !important; padding-left: 50px !important; }
        [data-testid="stForm"] div[data-baseweb="input"]:focus-within { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px #38bdf8 !important; }
        [data-testid="stFormSubmitButton"] button { background-color: #173775 !important; color: #ffffff !important; border: none !important; border-radius: 30px !important; height: 55px !important; font-weight: bold !important; font-size: 1.2em !important; margin-top: 20px !important; width: 100% !important; transition: all 0.3s ease !important; }
        [data-testid="stFormSubmitButton"] button:hover { background-color: #38bdf8 !important; box-shadow: 0 5px 15px rgba(56,189,248,0.4) !important; color: #ffffff !important; }
    </style>
    """, unsafe_allow_html=True)

if 'usuario' not in st.session_state:
    df_equipe = carregar_status_equipe()
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        lista_nomes = [n for n in df_equipe['Colaboradores'].tolist() if str(n).strip() != '']
        senhas = dict(zip(df_equipe['Colaboradores'], df_equipe.get('Senha', ['']*len(df_equipe))))
    else:
        lista_nomes = []; senhas = {}
        st.warning("⚠️ Planilha a carregar. Aguarde.")
        if st.button("🔄 Recarregar Nomes"): st.cache_data.clear(); st.rerun()

    render_corporate_login()
    def get_image_base64(caminho_imagem):
        try:
            with open(caminho_imagem, "rb") as img_file: return base64.b64encode(img_file.read()).decode()
        except: return "" 
    logo_b64 = get_image_base64("logo_frigelar.png")
    
    c1, c2, c3 = st.columns([1, 1.5, 1]) 
    with c2:
        with st.form("login_form", clear_on_submit=False):
            img_src = f"data:image/png;base64,{logo_b64}" if logo_b64 else "https://raichu-uploads.s3.amazonaws.com/logo_frigelar_QERmNQ.png"
            st.markdown(f'''
            <div align="center" style="margin-bottom: 25px;">
                <img src="{img_src}" style="width: 200px; display: block; margin: 0 auto; transform: translateX(-15px);">
                <h1 style="color: #173775; font-size: 2.4em; margin: 15px 0 5px 0; font-weight: 800;">BEM-VINDO</h1>
                <h2 style="color: #718096; font-size: 1.1em; margin: 0; font-weight: 400;">Sistema de Chamados</h2>
            </div>
            ''', unsafe_allow_html=True)
            user_digitado = st.text_input("Utilizador", placeholder="Coloque o seu usuário", label_visibility="collapsed")
            senha_digitada = st.text_input("Senha", type="password", placeholder="Coloque a sua senha", label_visibility="collapsed")
            submitted = st.form_submit_button("ENTRAR", use_container_width=True)
            if submitted:
                if user_digitado in lista_nomes and str(senha_digitada) == str(senhas.get(user_digitado, "")):
                    st.session_state['usuario'] = user_digitado
                    st.session_state['tamanho_fila_anterior'] = 0 
                    registrar_log(user_digitado, "LOGIN") 
                    st.rerun()
                else: st.error("❌ Login não encontrado ou senha incorreta.")

# ===================================================
# SISTEMA LOGADO
# ===================================================
else:
    usuario = st.session_state['usuario']
    
    df_qualitor = carregar_dados_chamados()
    df_azix_data = carregar_dados_azix()
    df_ativas_data = carregar_dados_ativas() 
    df_equipe = carregar_status_equipe()

    if usuario in SQUAD_AZIX or usuario in SQUAD_MKTP:
        df = df_azix_data
        aba_atual = aba_azix
    elif usuario in SQUAD_ATIVAS: 
        df = df_ativas_data
        aba_atual = aba_ativas
    else:
        df = df_qualitor
        aba_atual = aba_chamados

    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7

    hora_atual = hora_brasil().hour
    if 6 <= hora_atual < 12: saudacao = "Bom dia"; sub_saudacao = "Pronto para os chamados?"
    elif 12 <= hora_atual < 18: saudacao = "Boa tarde"; sub_saudacao = "Como está a esteira?"
    else: saudacao = "Boa noite"; sub_saudacao = "Quase na hora de descansar!"
    
    status_real = "Erro"
    linha_planilha = None
    minhas_etapas = ['Todas']
    
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        meus_dados = df_equipe[df_equipe['Colaboradores'] == usuario]
        if not meus_dados.empty:
            status_real = meus_dados.iloc[0].get('Status', 'Erro')
            etapas_str = str(meus_dados.iloc[0].get('Etapas_Permitidas', 'Todas'))
            minhas_etapas = [e.strip() for e in etapas_str.split(',')]
            idx = df_equipe.index[df_equipe['Colaboradores'] == usuario].tolist()[0]
            linha_planilha = idx + 2

    # --- BARRA LATERAL ---
    with st.sidebar:
        st.header(f"👤 {saudacao}, {usuario}!")
        modo_gerente = False
        if usuario in ADMINS:
            st.success("👑 Modo Gestor Liberado")
            modo_gerente = st.toggle("Painel de Gestão", value=False)
            st.divider()
            
        st.subheader("🏆 Seu Desempenho Hoje")
        
        qtd_minha = 0
        if not ranking_global.empty:
            minha_posicao = ranking_global.index[ranking_global['Nome'] == usuario].tolist()
            if minha_posicao:
                qtd_minha = ranking_global.iloc[minha_posicao[0]]['Qtd']
        
        pct = min(qtd_minha / META_DIARIA, 1.0) * 100
        cor_barra = "#ef4444" if pct < 50 else "#f59e0b" if pct < 100 else "#22c55e"
        
        st.markdown(f"""
        <div style="width: 100%; background-color: #e2e8f0; border-radius: 10px; margin-top: 5px;">
            <div style="width: {pct}%; background-color: {cor_barra}; height: 18px; border-radius: 10px; transition: 0.5s;"></div>
        </div>
        <p style="text-align: center; font-size: 13px; color: gray; margin-top: 5px;"><b>{qtd_minha}</b> de <b>{META_DIARIA}</b> chamados encerrados neste sistema</p>
        """, unsafe_allow_html=True)
        
        if not ranking_global.empty and minha_posicao:
            pos_real = minha_posicao[0] + 1
            if pos_real == 1: st.success(f"🥇 1º Lugar na equipe!")
            elif pos_real == 2: st.info(f"🥈 2º Lugar na equipe!")
            else: st.markdown(f"📍 **Sua posição no ranking:** {pos_real}º Lugar")
        else: st.caption("A corrida de hoje ainda não começou!")

        st.divider()
        with st.expander("⚙️ Meu Perfil"):
            if 'tema_escolhido' not in st.session_state: st.session_state['tema_escolhido'] = "Padrão"
            novo_tema = st.selectbox("🎨 Tema Visual", ["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"], index=["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"].index(st.session_state['tema_escolhido']))
            if novo_tema != st.session_state['tema_escolhido']:
                st.session_state['tema_escolhido'] = novo_tema
                st.rerun()
            st.divider()
            st.markdown("**Mudar Senha**")
            nova_senha = st.text_input("Digite a nova senha:", type="password")
            confirma_senha = st.text_input("Confirme a nova senha:", type="password")
            if st.button("Salvar Nova Senha", use_container_width=True):
                if nova_senha == confirma_senha and len(nova_senha) >= 4:
                    try:
                        cols_users = df_equipe.columns.tolist()
                        if "Senha" in cols_users:
                            col_senha_idx = cols_users.index("Senha") + 1
                            aba_users.update_cell(linha_planilha, col_senha_idx, nova_senha)
                            registrar_log(usuario, "Mudou a própria senha")
                            st.success("✅ Senha atualizada! Use no próximo login.")
                            st.cache_data.clear()
                        else: st.error("Erro: Coluna 'Senha' não encontrada.")
                    except Exception as e: st.error(f"Erro ao mudar senha: {e}")
                else: st.warning("⚠️ Senhas não batem ou são curtas (mín. 4).")
                
        st.divider()
        if st.button("Sair (Logout)"): registrar_log(usuario, "LOGOUT"); del st.session_state['usuario']; st.rerun()

    # ===================================================
    # 📺 MODO TELÃO (TV DO SALÃO)
    # ===================================================
    if usuario == "TV":
        st.markdown("<h1 style='text-align: center; color: #1E90FF; font-size: 50px;'>📺 Painel de Operações - Frigelar</h1>", unsafe_allow_html=True)
        st.markdown("""<style>[data-testid="stSidebar"], header {display: none;} .block-container {padding-top: 1rem; max-width: 98%;}</style>""", unsafe_allow_html=True)
        if st.button("Sair (Logout da TV)"): del st.session_state['usuario']; st.rerun()
        
        tot_q = len(df_qualitor)
        pend_q = len(df_qualitor[df_qualitor['Status'] == 'Pendente']) if not df_qualitor.empty else 0
        fora_sla_q = len(df_qualitor[(df_qualitor['Status'] == 'Pendente') & (df_qualitor['SLA'].astype(str).str.contains('Prioridade 1|fora', case=False, na=False))]) if not df_qualitor.empty and 'SLA' in df_qualitor.columns else 0
        dentro_sla_q = pend_q - fora_sla_q
        
        tot_a = len(df_azix_data)
        pend_a = len(df_azix_data[df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])]) if not df_azix_data.empty else 0
        fora_sla_a = len(df_azix_data[(df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])) & (df_azix_data['Status_SLA'].astype(str).str.contains('Atrasado|Vence Hoje', case=False, na=False))]) if not df_azix_data.empty and 'Status_SLA' in df_azix_data.columns else 0
        dentro_sla_a = pend_a - fora_sla_a
        
        azix_hoje_conc = len(logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Concluiu Azix", case=False)]) if not df_logs_global.empty else 0
        azix_hoje_avan = len(logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Azix para Mktp", case=False)]) if not df_logs_global.empty else 0
        
        colQ, colA = st.columns(2)
        with colQ:
            st.markdown("<div style='background-color: #f0f8ff; padding: 20px; border-radius: 10px; border-left: 8px solid #0284c7; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>", unsafe_allow_html=True)
            st.markdown("<h2 style='text-align: center; color: #0284c7; margin-top:0;'>🔷 OPERAÇÃO QUALITOR</h2>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center;'>📦 Total na Base: <b>{tot_q}</b></h3>", unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("🎫 Fila (Pendente)", pend_q)
            c2.metric("✅ No Prazo", dentro_sla_q)
            c3.metric("🔥 Atrasados/Críticos", fora_sla_q)
            st.markdown("</div>", unsafe_allow_html=True)
            
        with colA:
            st.markdown("<div style='background-color: #fffbeb; padding: 20px; border-radius: 10px; border-left: 8px solid #d97706; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>", unsafe_allow_html=True)
            st.markdown("<h2 style='text-align: center; color: #d97706; margin-top:0;'>🔶 OPERAÇÃO AZIX / MKTP</h2>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center;'>📦 Total na Base: <b>{tot_a}</b></h3>", unsafe_allow_html=True)
            c4, c5, c6 = st.columns(3)
            c4.metric("🎫 Fila Atual", pend_a)
            c5.metric("✅ No Prazo", dentro_sla_a)
            c6.metric("🔥 Atrasados", fora_sla_a)
            st.write("---")
            st.markdown("<p style='text-align: center; color: #92400e; margin-bottom:5px;'><b>📊 Desempenho Azix (Hoje):</b></p>", unsafe_allow_html=True)
            c7, c8 = st.columns(2)
            c7.metric("✅ Encerrados", azix_hoje_conc)
            c8.metric("⏭️ Passaram Etapa", azix_hoje_avan)
            st.markdown("</div>", unsafe_allow_html=True)

        st.write("---")
        st.markdown("<h3 style='text-align: center;'>🏆 DESTAQUES DO DIA (Equipe Geral)</h3>", unsafe_allow_html=True)
        if not ranking_global.empty:
            cols_rank = st.columns(min(3, len(ranking_global)))
            for i, row in ranking_global.head(3).iterrows():
                medalha = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                cols_rank[i].markdown(f"""
                <div style='background-color: white; padding: 15px; border-radius: 10px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;'>
                    <h2 style='margin:0;'>{medalha} {i+1}º Lugar</h2>
                    <h3 style='color: #475569; margin: 10px 0;'>{row['Nome']}</h3>
                    <h1 style='color: #10b981; margin:0;'>{row['Qtd']} <span style='font-size: 0.4em; color: gray;'>Concluídos</span></h1>
                </div>
                """, unsafe_allow_html=True)
        
        texto_mural = ler_mural()
        if texto_mural: 
            st.write("---")
            st.warning(f"📢 **AVISO DA GESTÃO:** {texto_mural}")
            
        time.sleep(15); st.cache_data.clear(); st.rerun()

    # ===================================================
    # 👑 VISÃO DO GESTOR (ADMIN)
    # ===================================================
    elif modo_gerente:
        st.title("📊 Painel de Controle - Gestão")
        st.caption(f"Última atualização: {hora_texto()}")
        if st.button("🔄 Atualizar Tudo (Limpar Cache)"): st.cache_data.clear(); st.rerun()
        
        st.write("---")
        st.subheader("📅 Máquina do Tempo (Filtro de Período dos Resultados)")
        c_dt1, c_dt2 = st.columns(2)
        hoje_date = hora_brasil().date()
        data_inicio = c_dt1.date_input("Data Inicial", hoje_date - timedelta(days=7), format="DD/MM/YYYY")
        data_fim = c_dt2.date_input("Data Final", hoje_date, format="DD/MM/YYYY")
        
        df_logs = carregar_logs_dia()
        ranking_periodo = pd.DataFrame()
        df_logs_periodo = pd.DataFrame()
        
        azix_concluidos_periodo = 0
        azix_avancados_periodo = 0
        azix_devolvidos_periodo = 0
        ativas_concluidos_periodo = 0
        importados_azix = 0
        
        if not df_logs.empty:
            df_logs['DataReal'] = pd.to_datetime(df_logs['DataHora'].str.split(' ').str[0], format="%d/%m/%Y", errors='coerce').dt.date
            df_logs_periodo = df_logs[(df_logs['DataReal'] >= data_inicio) & (df_logs['DataReal'] <= data_fim)].copy()
            
            feitos_periodo = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Finalizou|Encerrada|Concluiu Azix|Concluiu Ativa", case=False)]
            if not feitos_periodo.empty:
                ranking_periodo = feitos_periodo['Usuario'].value_counts().reset_index()
                ranking_periodo.columns = ['Nome', 'Qtd']
                
            azix_concluidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Concluiu Azix", case=False)])
            azix_avancados_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Azix para Mktp", case=False)])
            azix_devolvidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Devolveu Azix", case=False)])
            ativas_concluidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Concluiu Ativa", case=False)])
            
            logs_importacao = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Adicionou", case=False)]
            for acao in logs_importacao['Acao']:
                nums = re.findall(r'\d+', str(acao))
                if nums: importados_azix += int(nums[0])

        st.write("---")
        st.subheader("📈 Análise de Produtividade e Filas (Tempo Real e Período)")
        
        pend_q = len(df_qualitor[df_qualitor['Status'] == 'Pendente']) if not df_qualitor.empty else 0
        fora_sla_q = len(df_qualitor[(df_qualitor['Status'] == 'Pendente') & (df_qualitor['SLA'].astype(str).str.contains('Prioridade 1|fora', case=False, na=False))]) if not df_qualitor.empty and 'SLA' in df_qualitor.columns else 0
        
        pend_a = len(df_azix_data[df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])]) if not df_azix_data.empty else 0
        fora_sla_a = len(df_azix_data[(df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])) & (df_azix_data['Status_SLA'].astype(str).str.contains('Atrasado|Vence Hoje', case=False, na=False))]) if not df_azix_data.empty and 'Status_SLA' in df_azix_data.columns else 0
        
        pend_ativas = len(df_ativas_data[df_ativas_data['Status'] == 'Pendente']) if not df_ativas_data.empty else 0
        prio1_ativas = len(df_ativas_data[(df_ativas_data['Status'] == 'Pendente') & (df_ativas_data['Prioridade'].astype(str) == '1')]) if not df_ativas_data.empty and 'Prioridade' in df_ativas_data.columns else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.markdown(f"""
        <div style='background-color: #f0f9ff; padding: 15px; border-radius: 10px; border-left: 5px solid #0284c7; margin-bottom: 20px;'>
            <h4 style='color: #0369a1; margin-top:0;'>📦 Qualitor (SAC)</h4>
            <h2>{pend_q} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Pendentes</span></h2>
            <p style='color: #dc2626; margin-bottom:0;'>🔥 {fora_sla_q} Atrasados/Críticos</p>
        </div>
        """, unsafe_allow_html=True)
        
        col2.markdown(f"""
        <div style='background-color: #fff7ed; padding: 15px; border-radius: 10px; border-left: 5px solid #d97706; margin-bottom: 20px;'>
            <h4 style='color: #b45309; margin-top:0;'>🔶 Azix Tratativas</h4>
            <h2>{pend_a} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Pendentes</span></h2>
            <p style='color: #dc2626; margin-bottom:0;'>🔥 {fora_sla_a} Atrasados/Críticos</p>
        </div>
        """, unsafe_allow_html=True)

        col3.markdown(f"""
        <div style='background-color: #faf5ff; padding: 15px; border-radius: 10px; border-left: 5px solid #8b5cf6; margin-bottom: 20px;'>
            <h4 style='color: #6b21a8; margin-top:0;'>🎯 Ativas Mktp</h4>
            <h2>{pend_ativas} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Pendentes</span></h2>
            <p style='color: #dc2626; margin-bottom:0;'>🔥 {prio1_ativas} Prioridade 1</p>
            <p style='color: #6b21a8; font-size: 0.8em; margin-top:5px; margin-bottom:0;'>✅ Concluídos no Período: <b>{ativas_concluidos_periodo}</b></p>
        </div>
        """, unsafe_allow_html=True)
        
        total_feitos_periodo = ranking_periodo['Qtd'].sum() if not ranking_periodo.empty else 0
        col4.markdown(f"""
        <div style='background-color: #f0fdf4; padding: 15px; border-radius: 10px; border-left: 5px solid #16a34a; margin-bottom: 20px;'>
            <h4 style='color: #15803d; margin-top:0;'>✅ Produtividade Geral</h4>
            <h2>{total_feitos_periodo} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Concluídos</span></h2>
            <p style='color: #16a34a; margin-bottom:0;'>De {data_inicio.strftime('%d/%m')} até {data_fim.strftime('%d/%m')}</p>
        </div>
        """, unsafe_allow_html=True)

        c_graf1, c_graf2 = st.columns(2)
        with c_graf1:
            st.markdown("**Status Atual da Operação (Qualitor + Azix + Ativas)**")
            if not df_qualitor.empty and not df_azix_data.empty:
                status_comb = pd.concat([df_qualitor['Status'], df_azix_data['Status']])
                if not df_ativas_data.empty:
                    status_comb = pd.concat([status_comb, df_ativas_data['Status']])
                st.bar_chart(status_comb.value_counts(), use_container_width=True)
            else: st.info("Sem dados")
        with c_graf2:
            st.markdown(f"**Top Produtividade ({data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m')})**")
            if not ranking_periodo.empty: st.bar_chart(ranking_periodo.set_index('Nome'), use_container_width=True)
            else: st.info("Sem dados para o período selecionado.")

        st.write("---")
        st.subheader("👥 Força de Trabalho e Dimensionamento (WFM)")
        
        hoje_str = data_hoje()
        logs_do_dia = df_logs[df_logs['DataHora'].astype(str).str.contains(hoje_str)] if not df_logs.empty else pd.DataFrame()
        
        qtd_azix_hoje = 0
        qtd_qualitor_hoje = 0
        qtd_ativas_hoje = 0
        
        if not logs_do_dia.empty:
            usuarios_logados = logs_do_dia['Usuario'].unique()
            ops_reais = [u for u in usuarios_logados if u not in ADMINS and u != "TV"]
            
            ops_azix = [u for u in ops_reais if u in SQUAD_AZIX or u in SQUAD_MKTP]
            ops_ativas = [u for u in ops_reais if u in SQUAD_ATIVAS]
            ops_qualitor = [u for u in ops_reais if u not in SQUAD_AZIX and u not in SQUAD_MKTP and u not in SQUAD_ATIVAS]
            
            qtd_azix_hoje = len(ops_azix)
            qtd_qualitor_hoje = len(ops_qualitor)
            qtd_ativas_hoje = len(ops_ativas)

        c_wfm1, c_wfm2, c_wfm3 = st.columns(3)
        with c_wfm1:
            st.markdown(f"<div style='background-color: #f0f9ff; padding: 15px; border-radius: 8px; border-left: 5px solid #0284c7;'><h4>Operadores Qualitor: {qtd_qualitor_hoje}</h4></div>", unsafe_allow_html=True)
        with c_wfm2:
            st.markdown(f"<div style='background-color: #fff7ed; padding: 15px; border-radius: 8px; border-left: 5px solid #d97706;'><h4>Operadores Azix: {qtd_azix_hoje}</h4></div>", unsafe_allow_html=True)
        with c_wfm3:
            st.markdown(f"<div style='background-color: #faf5ff; padding: 15px; border-radius: 8px; border-left: 5px solid #8b5cf6;'><h4>Operadores Ativas: {qtd_ativas_hoje}</h4></div>", unsafe_allow_html=True)

        # ====================================================================
        # ⏱️ MOTOR DE TMA (ON-DEMAND)
        # ====================================================================
        st.write("---")
        with st.expander("⏱️ Desempenho de Tempo (TMA / TMT) - Clique para Abrir", expanded=False):
            if st.button("🚀 Calcular TMA do Período", type="primary"):
                if not df_logs_periodo.empty:
                    with st.spinner("Analisando logs do Firebase..."):
                        try:
                            df_tma = df_logs_periodo.copy()
                            df_tma['ID_Chamado'] = df_tma['Acao'].astype(str).str.extract(r'(\d+)')
                            
                            def classificar_acao(texto):
                                texto = str(texto).lower()
                                if 'pegou' in texto or 'busca ativa' in texto: return 'Inicio'
                                if any(x in texto for x in ['finalizou', 'concluiu', 'encerrada']): return 'Fim'
                                return 'Outro'
                            
                            df_tma['Tipo_Acao'] = df_tma['Acao'].apply(classificar_acao)
                            df_calc = df_tma[(df_tma['ID_Chamado'].notna()) & (df_tma['Tipo_Acao'].isin(['Inicio', 'Fim']))].copy()
                            
                            if not df_calc.empty:
                                df_calc['DataHora_DT'] = pd.to_datetime(df_calc['DataHora'], format="%d/%m/%Y %H:%M:%S", errors='coerce')
                                tma_pivot = df_calc.pivot_table(index=['Usuario', 'ID_Chamado'], columns='Tipo_Acao', values='DataHora_DT', aggfunc='first').reset_index()
                                
                                if 'Inicio' in tma_pivot.columns and 'Fim' in tma_pivot.columns:
                                    tma_pivot['Duracao_Minutos'] = (tma_pivot['Fim'] - tma_pivot['Inicio']).dt.total_seconds() / 60.0
                                    tma_pivot = tma_pivot[tma_pivot['Duracao_Minutos'] > 0]
                                    
                                    if not tma_pivot.empty:
                                        def definir_squad(user):
                                            if user in SQUAD_AZIX or user in SQUAD_MKTP: return "🔶 Azix/Mktp"
                                            if user in SQUAD_ATIVAS: return "🎯 Ativas Mktp"
                                            return "🔷 Qualitor"
                                        
                                        tma_pivot['Equipe'] = tma_pivot['Usuario'].apply(definir_squad)
                                        tma_geral = tma_pivot['Duracao_Minutos'].mean()
                                        tma_por_squad = tma_pivot.groupby('Equipe')['Duracao_Minutos'].mean().reset_index()
                                        tma_por_user = tma_pivot.groupby(['Equipe', 'Usuario'])['Duracao_Minutos'].agg(['mean', 'count']).reset_index()
                                        tma_por_user.columns = ['Equipe', 'Operador', 'TMA_Minutos', 'Chamados_Medidos']
                                        
                                        def formatar_tma(minutos):
                                            if pd.isna(minutos): return "-"
                                            m = int(minutos)
                                            s = int((minutos - m) * 60)
                                            return f"{m}m {s}s"
                                        
                                        col_t1, col_t2 = st.columns([1, 2])
                                        with col_t1:
                                            st.markdown(f"<h3>TMA Global: {formatar_tma(tma_geral)}</h3>", unsafe_allow_html=True)
                                            tma_por_squad['TMA Visual'] = tma_por_squad['Duracao_Minutos'].apply(formatar_tma)
                                            st.dataframe(tma_por_squad[['Equipe', 'TMA Visual']], hide_index=True, use_container_width=True)
                                        with col_t2:
                                            tma_por_user['TMA Visual'] = tma_por_user['TMA_Minutos'].apply(formatar_tma)
                                            st.dataframe(tma_por_user[['Equipe', 'Operador', 'TMA Visual', 'Chamados_Medidos']], hide_index=True, use_container_width=True)
                        except Exception as e:
                            st.error(f"Erro ao calcular TMA: {e}")

    # =========================================================================
    # SQUADS DE ATENDIMENTO (QUALITOR, AZIX, MKTP, ATIVAS)
    # =========================================================================
    elif usuario in SQUAD_AZIX:
        st.title("🛡️ Painel Azix - Tratativas")
        # [Mantenha a lógica Azix igualzinha...]
        st.info("Painel Azix ativo.")

    elif usuario in SQUAD_MKTP:
        st.title("🛒 Painel de Reivindicações (Mktp)")
        # [Mantenha a lógica Mktp igualzinha...]
        st.info("Painel Mktp ativo.")

    elif usuario in SQUAD_ATIVAS:
        st.title("Marketplace (Ativas)")
        # [Mantenha a lógica Ativas igualzinha...]
        st.info("Painel Ativas ativo.")

    else:
        st.success(f"🟢 ONLINE - {sub_saudacao}")
        # [Mantenha a lógica Qualitor padrão igualzinha...]
        st.info("Painel Qualitor ativo.")
